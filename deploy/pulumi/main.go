package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"

	"github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes"
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/core/v1"
	helmv3 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi/config"
)

var digestPattern = regexp.MustCompile(`^sha256:[a-f0-9]{64}$`)
var identifierPattern = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)
var commitPattern = regexp.MustCompile(`^[a-f0-9]{40}$`)

func pinnedImage(image string) (string, string, error) {
	parts := strings.Split(image, "@")
	if len(parts) != 2 || parts[0] == "" || !digestPattern.MatchString(parts[1]) {
		return "", "", fmt.Errorf("image must include repository@sha256:digest")
	}
	return parts[0], parts[1], nil
}

func requiredEnv(name string) (string, error) {
	v := os.Getenv(name)
	if v == "" {
		return "", fmt.Errorf("%s must be configured", name)
	}
	return v, nil
}

func secretString(v string) pulumi.StringOutput {
	return pulumi.ToSecret(pulumi.String(v)).(pulumi.StringOutput)
}

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		freeze := strings.ToLower(strings.TrimSpace(os.Getenv("AML_FREEZE")))
		if freeze != "" && freeze != "false" && freeze != "true" {
			return fmt.Errorf("AML_FREEZE must be true or false")
		}
		if !ctx.DryRun() && freeze == "true" {
			return fmt.Errorf("deployment blocked by AML_FREEZE=true")
		}
		if ctx.Project() != "memory-benchmarks" || ctx.Stack() != "new-dev" {
			return fmt.Errorf("this program only supports memory-benchmarks/new-dev")
		}
		cfg := config.New(ctx, "")
		workers := 1
		if raw := os.Getenv("AML_ADAPTER_WORKERS"); raw != "" {
			var err error
			workers, err = strconv.Atoi(raw)
			if err != nil || workers < 1 {
				return fmt.Errorf("AML_ADAPTER_WORKERS must be a positive integer")
			}
		}
		namespace := cfg.Require("namespace")
		if namespace != "aml-memory-benchmarks" {
			return fmt.Errorf("namespace must be aml-memory-benchmarks")
		}
		values := map[string]string{}
		for _, key := range []string{"AML_ADAPTER_IMAGE", "AML_MEMORIA_IMAGE", "AML_DATABASE_PASSWORD", "AML_MEMORIA_MASTER_KEY", "AML_ADAPTER_API_KEY", "AML_EMBEDDING_API_KEY", "AML_REGISTRY_USERNAME", "AML_REGISTRY_PASSWORD"} {
			value, err := requiredEnv(key)
			if err != nil {
				return err
			}
			values[key] = value
		}
		if len(values["AML_MEMORIA_MASTER_KEY"]) < 32 || len(values["AML_ADAPTER_API_KEY"]) < 32 {
			return fmt.Errorf("Memoria and adapter keys must each contain at least 32 characters")
		}
		if values["AML_MEMORIA_MASTER_KEY"] == values["AML_ADAPTER_API_KEY"] {
			return fmt.Errorf("Memoria and adapter keys must be different")
		}
		adapterRepo, adapterDigest, err := pinnedImage(values["AML_ADAPTER_IMAGE"])
		if err != nil {
			return err
		}
		memoriaRepo, memoriaDigest, err := pinnedImage(values["AML_MEMORIA_IMAGE"])
		if err != nil {
			return err
		}
		sourceRepo, sourceRef, sourceCommit := os.Getenv("AML_MEMORIA_SOURCE_REPOSITORY"), os.Getenv("AML_MEMORIA_SOURCE_REF"), os.Getenv("AML_MEMORIA_SOURCE_COMMIT")
		if (sourceRepo != "" || sourceRef != "" || sourceCommit != "") && (sourceRepo == "" || sourceRef == "" || !commitPattern.MatchString(sourceCommit)) {
			return fmt.Errorf("Memoria source metadata must include repository, ref and full commit SHA")
		}
		account, user, database, role := cfg.Require("databaseAccount"), cfg.Require("databaseUser"), cfg.Require("databaseName"), cfg.Require("databaseRole")
		for _, name := range []string{user, database, role} {
			if !identifierPattern.MatchString(name) {
				return fmt.Errorf("database names must be lowercase SQL identifiers")
			}
		}
		if account == "" || strings.ContainsAny(account, ":/@") || user != "aml_memoria" || database != "aml_memoria" || role != "aml_memoria_role" {
			return fmt.Errorf("use the dedicated aml_memoria user, role and database")
		}
		// The workflow preflight verifies both the API server and kube-system UID.
		if os.Getenv("AML_VERIFIED_CLUSTER_UID") != cfg.Require("expectedClusterUID") {
			return fmt.Errorf("run deploy/scripts/preflight.py before deploying")
		}
		kubeconfig, err := os.ReadFile(os.Getenv("KUBECONFIG"))
		if err != nil {
			return fmt.Errorf("cannot read the temporary kubeconfig")
		}
		provider, err := kubernetes.NewProvider(ctx, "aliyun-dev-unit", &kubernetes.ProviderArgs{
			Kubeconfig:            secretString(string(kubeconfig)),
			EnableServerSideApply: pulumi.Bool(false),
		})
		if err != nil {
			return err
		}
		ns, err := corev1.NewNamespace(ctx, "aml-namespace", &corev1.NamespaceArgs{
			Metadata: &metav1.ObjectMetaArgs{Name: pulumi.String(namespace)},
		}, pulumi.Provider(provider), pulumi.Protect(true))
		if err != nil {
			return err
		}
		dbURL := (&url.URL{Scheme: "mysql", User: url.UserPassword(account+":"+user+":"+role, values["AML_DATABASE_PASSWORD"]), Host: net.JoinHostPort(cfg.Require("databaseHost"), cfg.Require("databasePort")), Path: "/" + database}).String()
		secretValues := map[string]string{
			"DATABASE_URL": dbURL, "MASTER_KEY": values["AML_MEMORIA_MASTER_KEY"],
			"ADAPTER_API_KEY": values["AML_ADAPTER_API_KEY"], "EMBEDDING_API_KEY": values["AML_EMBEDDING_API_KEY"],
		}
		secretData := pulumi.StringMap{}
		for k, v := range secretValues {
			secretData[k] = secretString(v)
		}
		auth, err := corev1.NewSecret(ctx, "runtime-secrets", &corev1.SecretArgs{
			Metadata:   &metav1.ObjectMetaArgs{Name: pulumi.String("aml-runtime"), Namespace: pulumi.String(namespace)},
			StringData: secretData,
		}, pulumi.Provider(provider), pulumi.DependsOn([]pulumi.Resource{ns}))
		if err != nil {
			return err
		}
		// A checksum forces a restart when credentials change; it stays a Pulumi secret.
		encoded, _ := json.Marshal(secretValues)
		checksum := sha256.Sum256(encoded)
		dockerAuth := base64.StdEncoding.EncodeToString([]byte(values["AML_REGISTRY_USERNAME"] + ":" + values["AML_REGISTRY_PASSWORD"]))
		dockerConfig, _ := json.Marshal(map[string]any{"auths": map[string]any{
			"registry.cn-hangzhou.aliyuncs.com":     map[string]string{"auth": dockerAuth},
			"registry-vpc.cn-hangzhou.aliyuncs.com": map[string]string{"auth": dockerAuth},
		}})
		registry, err := corev1.NewSecret(ctx, "registry-pull", &corev1.SecretArgs{
			Metadata:   &metav1.ObjectMetaArgs{Name: pulumi.String("aml-registry"), Namespace: pulumi.String(namespace)},
			Type:       pulumi.String("kubernetes.io/dockerconfigjson"),
			StringData: pulumi.StringMap{".dockerconfigjson": secretString(string(dockerConfig))},
		}, pulumi.Provider(provider), pulumi.DependsOn([]pulumi.Resource{ns}))
		if err != nil {
			return err
		}
		accessMode := cfg.Require("accessMode")
		if accessMode != "ReadWriteOnce" {
			return fmt.Errorf("single-writer journal requires ReadWriteOnce")
		}
		state, err := corev1.NewPersistentVolumeClaim(ctx, "adapter-journal", &corev1.PersistentVolumeClaimArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name: pulumi.String("aml-adapter-state"), Namespace: pulumi.String(namespace),
				Annotations: pulumi.StringMap{"pulumi.com/skipAwait": pulumi.String("true")},
			},
			Spec: &corev1.PersistentVolumeClaimSpecArgs{
				AccessModes:      pulumi.StringArray{pulumi.String(accessMode)},
				StorageClassName: pulumi.String(cfg.Require("storageClass")),
				Resources:        &corev1.VolumeResourceRequirementsArgs{Requests: pulumi.StringMap{"storage": pulumi.String(cfg.Require("storageSize"))}},
			},
		}, pulumi.Provider(provider), pulumi.DependsOn([]pulumi.Resource{ns}), pulumi.Protect(true))
		if err != nil {
			return err
		}
		host := os.Getenv("AML_PUBLIC_HOST")
		tlsSecret := os.Getenv("AML_TLS_SECRET")
		if tlsSecret == "" {
			tlsSecret = "aml-tls"
		}
		certificateIssuer := os.Getenv("AML_CERT_ISSUER")
		if certificateIssuer == "" {
			certificateIssuer = cfg.Require("certificateIssuer")
		}
		_, err = helmv3.NewRelease(ctx, "aml-services", &helmv3.ReleaseArgs{
			Name: pulumi.String("aml"), Namespace: pulumi.String(namespace),
			Chart: pulumi.String("../chart"), CreateNamespace: pulumi.Bool(false),
			Timeout: pulumi.Int(2400), MaxHistory: pulumi.Int(5), SkipAwait: pulumi.Bool(false),
			Values: pulumi.Map{
				"image":           pulumi.Map{"repository": pulumi.String(adapterRepo), "digest": pulumi.String(adapterDigest)},
				"imagePullSecret": registry.Metadata.Name(), "authSecret": auth.Metadata.Name(), "stateClaim": state.Metadata.Name(),
				"secretChecksum":  secretString(hex.EncodeToString(checksum[:])),
				"namespacePrefix": pulumi.String(cfg.Require("ownerPrefix")),
				"workers":         pulumi.Int(workers),
				"memoria": pulumi.Map{
					"sourceRepository": pulumi.String(sourceRepo), "sourceRef": pulumi.String(sourceRef), "sourceCommit": pulumi.String(sourceCommit),
					"image": pulumi.Map{"repository": pulumi.String(memoriaRepo), "digest": pulumi.String(memoriaDigest)},
					"url":   pulumi.String("http://aml-memoria:8100"), "secretName": auth.Metadata.Name(), "secretKey": pulumi.String("MASTER_KEY"),
					"embeddingModel": pulumi.String(cfg.Require("embeddingModel")), "embeddingDim": pulumi.String(cfg.Require("embeddingDim")), "embeddingBaseURL": pulumi.String(cfg.Require("embeddingBaseURL")),
				},
				"ingress":     pulumi.Map{"enabled": pulumi.Bool(host != ""), "className": pulumi.String(cfg.Require("ingressClass")), "host": pulumi.String(host), "tlsSecret": pulumi.String(tlsSecret)},
				"certificate": pulumi.Map{"issuer": pulumi.String(certificateIssuer)},
			},
		}, pulumi.Provider(provider), pulumi.DependsOn([]pulumi.Resource{auth, registry, state}))
		if err != nil {
			return err
		}
		ctx.Export("namespace", pulumi.String(namespace))
		ctx.Export("adapterService", pulumi.String("aml"))
		ctx.Export("memoriaService", pulumi.String("aml-memoria"))
		ctx.Export("adapterImage", pulumi.String(values["AML_ADAPTER_IMAGE"]))
		ctx.Export("memoriaImage", pulumi.String(values["AML_MEMORIA_IMAGE"]))
		if sourceCommit != "" {
			ctx.Export("memoriaSourceRepository", pulumi.String(sourceRepo))
			ctx.Export("memoriaSourceRef", pulumi.String(sourceRef))
			ctx.Export("memoriaSourceCommit", pulumi.String(sourceCommit))
		}
		if host != "" {
			ctx.Export("publicURL", pulumi.String("https://"+host))
		}
		return nil
	})
}

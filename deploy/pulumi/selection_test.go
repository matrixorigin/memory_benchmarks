package main

import (
	"reflect"
	"testing"
	"time"

	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func resolveValues(t *testing.T, values pulumi.Map) map[string]any {
	t.Helper()
	result := make(chan map[string]any, 1)
	pulumi.ToOutput(values).ApplyT(func(v map[string]any) map[string]any {
		result <- v
		return v
	})
	select {
	case v := <-result:
		return v
	case <-time.After(5 * time.Second):
		t.Fatal("timed out resolving selected release values")
		return nil
	}
}

func TestPartialReleasePreservesUnselectedApplicationAndSharedValues(t *testing.T) {
	retained := map[string]any{
		"image": map[string]any{"repository": "old-adapter", "digest": "old-adapter-digest"},
		"memoria": map[string]any{
			"image": map[string]any{"repository": "old-memoria", "digest": "old-memoria-digest"},
			"url":   "http://existing-memoria", "secretName": "old-secret", "secretKey": "MASTER_KEY",
			"sourceCommit": "old-commit", "embeddingModel": "old-model",
		},
		"secretChecksum": "existing-checksum", "namespacePrefix": "old-owner", "workers": float64(4),
		"ingress":   map[string]any{"enabled": true, "host": "existing.example.invalid"},
		"resources": map[string]any{"limits": map[string]any{"memory": "1Gi"}},
	}
	desired := pulumi.Map{
		"image": pulumi.Map{"repository": pulumi.String("new-adapter")},
		"memoria": pulumi.Map{
			"image": pulumi.Map{"repository": pulumi.String("new-memoria")},
			"url":   pulumi.String("http://new-url"), "secretName": pulumi.String("new-secret"),
			"secretKey": pulumi.String("new-key"), "embeddingModel": pulumi.String("new-model"),
		},
		"workers": pulumi.Int(2), "namespacePrefix": pulumi.String("new-owner"),
		"secretChecksum": pulumi.String("new-checksum"),
		"ingress":        pulumi.Map{"enabled": pulumi.Bool(false)},
	}
	for _, target := range []string{"memoria", "memory_benchmarks"} {
		t.Run(target, func(t *testing.T) {
			selected, err := selectValues(desired, target, retained)
			if err != nil {
				t.Fatal(err)
			}
			got := resolveValues(t, selected)
			for _, key := range []string{"secretChecksum", "ingress", "resources"} {
				if !reflect.DeepEqual(got[key], retained[key]) {
					t.Fatalf("shared value %s changed: %#v", key, got[key])
				}
			}
			if target == "memory_benchmarks" {
				if !reflect.DeepEqual(got["memoria"], retained["memoria"]) {
					t.Fatal("Memoria configuration changed")
				}
				if got["image"].(map[string]any)["repository"] != "new-adapter" {
					t.Fatal("adapter was not updated")
				}
			} else {
				for _, key := range []string{"image", "workers", "namespacePrefix"} {
					if !reflect.DeepEqual(got[key], retained[key]) {
						t.Fatalf("adapter value %s changed", key)
					}
				}
				m := got["memoria"].(map[string]any)
				if m["embeddingModel"] != "new-model" {
					t.Fatal("Memoria was not updated")
				}
				for _, key := range []string{"url", "secretName", "secretKey"} {
					if m[key] != retained["memoria"].(map[string]any)[key] {
						t.Fatalf("adapter connection %s changed", key)
					}
				}
			}
		})
	}
}

func TestSelectionRequiresCompleteExistingDeployment(t *testing.T) {
	for _, target := range []string{"invalid", "memoria", "memory_benchmarks"} {
		if _, err := selectValues(pulumi.Map{}, target, nil); err == nil {
			t.Fatalf("target %s accepted without a complete existing deployment", target)
		}
	}
	desired := pulumi.Map{"image": pulumi.String("fresh")}
	selected, err := selectValues(desired, "both", nil)
	if err != nil || !reflect.DeepEqual(selected, desired) {
		t.Fatal("both should permit initial deployment")
	}
}

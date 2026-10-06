package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

// A partial release starts from the deployed values, including chart defaults.
// Only the selected app receives its newly generated configuration. Shared
// credentials are checked against the live Secret by deployment_target.py.
func selectValues(desired pulumi.Map, target string, retained map[string]any) (pulumi.Map, error) {
	if target == "" || target == "both" {
		return desired, nil
	}
	if target != "memoria" && target != "memory_benchmarks" {
		return nil, fmt.Errorf("deployment_target must be both, memoria, or memory_benchmarks")
	}
	for _, key := range []string{"image", "memoria", "secretChecksum", "namespacePrefix", "workers"} {
		if retained[key] == nil {
			return nil, fmt.Errorf("partial updates require an existing complete deployment; deploy both first")
		}
	}
	selected := pulumi.Map{}
	for key, value := range retained {
		selected[key] = pulumi.Any(value)
	}
	checksum, ok := retained["secretChecksum"].(string)
	if !ok || checksum == "" {
		return nil, fmt.Errorf("existing deployment has no runtime checksum; deploy both first")
	}
	selected["secretChecksum"] = secretString(checksum)
	if target == "memory_benchmarks" {
		for _, key := range []string{"image", "namespacePrefix", "workers"} {
			selected[key] = desired[key]
		}
	} else {
		old, ok := retained["memoria"].(map[string]any)
		if !ok {
			return nil, fmt.Errorf("invalid retained Memoria configuration")
		}
		next, ok := desired["memoria"].(pulumi.Map)
		if !ok {
			return nil, fmt.Errorf("invalid selected Memoria configuration")
		}
		copied := pulumi.Map{}
		for key, value := range next {
			copied[key] = value
		}
		// These fields are consumed by the adapter pod. Preserve them even when
		// Memoria's image, source metadata, or embedding settings are updated.
		for _, key := range []string{"url", "secretName", "secretKey"} {
			value, ok := old[key].(string)
			if !ok || value == "" {
				return nil, fmt.Errorf("incomplete retained adapter connection; deploy both first")
			}
			copied[key] = pulumi.String(value)
		}
		selected["memoria"] = copied
	}
	return selected, nil
}

func selectedReleaseValues(desired pulumi.Map) (pulumi.Map, error) {
	target := os.Getenv("AML_DEPLOYMENT_TARGET")
	if target == "" || target == "both" {
		return desired, nil
	}
	content, err := os.ReadFile(os.Getenv("AML_RETAINED_VALUES_FILE"))
	if err != nil {
		return nil, fmt.Errorf("run deployment_target.py reuse before a partial update")
	}
	var snapshot struct {
		Target string         `json:"deployment_target"`
		Values map[string]any `json:"values"`
	}
	if json.Unmarshal(content, &snapshot) != nil || snapshot.Target != target {
		return nil, fmt.Errorf("retained deployment snapshot does not match deployment_target")
	}
	return selectValues(desired, target, snapshot.Values)
}

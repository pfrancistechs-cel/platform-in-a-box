# Dockerfile rules (conftest parses a Dockerfile into a list of instructions: Cmd and Value).
package dockerfile

import rego.v1

stage_names contains lower(i.Value[2]) if {
	some i in input
	i.Cmd == "from"
	count(i.Value) == 3
}

deny contains msg if {
	some i in input
	i.Cmd == "from"
	image := i.Value[0]
	image != "scratch"
	not lower(image) in stage_names
	not pinned(image)
	msg := sprintf("DKR-001 FROM %s has no fixed tag (no tag, or latest)", [image])
}

pinned(image) if regex.match(`@sha256:[a-f0-9]{64}$`, image)

pinned(image) if {
	parts := split(image, "/")
	last := parts[count(parts) - 1]
	contains(last, ":")
	split(last, ":")[1] != "latest"
}

users := [i.Value[0] | some i in input; i.Cmd == "user"]

deny contains "DKR-002 the image runs as root (add a USER instruction with a non-root user)" if {
	count(users) == 0
}

deny contains msg if {
	count(users) > 0
	last := users[count(users) - 1]
	regex.match(`^(root|0)(:.*)?$`, last)
	msg := sprintf("DKR-002 the last USER is '%s' (root)", [last])
}

deny contains msg if {
	some i in input
	i.Cmd == "add"
	regex.match(`^https?://`, i.Value[0])
	msg := sprintf("DKR-003 ADD downloads %s at build time (use COPY, or curl with a checksum)", [i.Value[0]])
}

warn contains "DKR-004 no HEALTHCHECK instruction" if {
	not has_healthcheck
}

has_healthcheck if {
	some i in input
	i.Cmd == "healthcheck"
}

deny contains msg if {
	some i in input
	i.Cmd in {"env", "arg"}
	regex.match(`(?i)(password|passwd|secret|token|api_?key)`, i.Value[0])
	count(i.Value) > 1
	msg := sprintf("DKR-005 %s sets a value for '%s' (secrets do not belong in image layers)", [upper(i.Cmd), i.Value[0]])
}

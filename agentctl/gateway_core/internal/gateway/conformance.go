// Golden-wire conformance builder (Workstream 4): builds a Frame from the shared,
// language-neutral spec in tests/fixtures/conformance_frames.json — the SAME file the Python
// suite (tests/test_conformance.py) reads. Both runtimes marshal deterministically and assert the
// bytes equal the committed golden_hex, proving the Go data plane and the Python reference proxy
// are wire-identical for the frozen Frame envelope.
package gateway

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"

	"google.golang.org/protobuf/proto"

	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
)

// Payload mirrors the union of all oneof payload fields in the JSON spec. bytes fields are hex.
type Payload struct {
	Kind string `json:"kind"`
	// text
	Content string `json:"content"`
	Partial bool   `json:"partial"`
	// tool_call
	CallID        string `json:"call_id"`
	ToolName      string `json:"tool_name"`
	ArgumentsHex  string `json:"arguments_hex"`
	SideEffecting bool   `json:"side_effecting"`
	// tool_result
	ResultHex string `json:"result_hex"`
	IsError   bool   `json:"is_error"`
	Mocked    bool   `json:"mocked"`
	// control
	ControlKind string      `json:"control_kind"`
	Reason      string      `json:"reason"` // control: free text; turn_end: FinishReason enum name
	AckSeq      json.Number `json:"ack_seq"`
	Credits     json.Number `json:"credits"`
	// turn_end
	TurnID           string      `json:"turn_id"`
	PromptTokens     json.Number `json:"prompt_tokens"`
	CompletionTokens json.Number `json:"completion_tokens"`
	// binary
	Modality string      `json:"modality"`
	Codec    string      `json:"codec"`
	GroupID  json.Number `json:"group_id"`
	Index    json.Number `json:"index"`
	Last     bool        `json:"last"`
	DataHex  string      `json:"data_hex"`
	Width    json.Number `json:"width"`
	Height   json.Number `json:"height"`
	PtsNanos json.Number `json:"pts_nanos"`
	// approval_req
	ApprovalID       string      `json:"approval_id"`
	ActionSummary    string      `json:"action_summary"`
	ActionPayloadHex string      `json:"action_payload_hex"`
	ExpiresAtUnix    json.Number `json:"expires_at_unix"`
	Risk             string      `json:"risk"`
}

// Spec mirrors one entry in the fixture's "frames" array.
type Spec struct {
	Name        string            `json:"name"`
	SessionID   string            `json:"session_id"`
	StreamID    json.Number       `json:"stream_id"`
	Seq         json.Number       `json:"seq"`
	Direction   string            `json:"direction"`
	TsUnixNanos json.Number       `json:"ts_unix_nanos"`
	Attributes  map[string]string `json:"attributes"`
	Payload     Payload           `json:"payload"`
	GoldenHex   string            `json:"golden_hex"`
	HeaderHex   string            `json:"header_hex"`
}

func u64(n json.Number) uint64  { v, _ := strconv.ParseUint(n.String(), 10, 64); return v }
func u32(n json.Number) uint32  { v, _ := strconv.ParseUint(n.String(), 10, 32); return uint32(v) }
func i64(n json.Number) int64   { if n == "" { return 0 }; v, _ := n.Int64(); return v }
func hexb(s string) []byte      { b, _ := hex.DecodeString(s); return b }

// BuildFrame constructs a Frame from a spec, mirroring tests/conformance_frames.py::build_frame.
func BuildFrame(s Spec) (*acpv1.Frame, error) {
	f := &acpv1.Frame{
		SessionId:   s.SessionID,
		StreamId:    u64(s.StreamID),
		Seq:         u64(s.Seq),
		Direction:   acpv1.Direction(acpv1.Direction_value[s.Direction]),
		TsUnixNanos: i64(s.TsUnixNanos),
	}
	p := s.Payload
	switch p.Kind {
	case "text":
		f.Payload = &acpv1.Frame_Text{Text: &acpv1.TextDelta{Content: p.Content, Partial: p.Partial}}
	case "tool_call":
		f.Payload = &acpv1.Frame_ToolCall{ToolCall: &acpv1.ToolCall{
			CallId: p.CallID, ToolName: p.ToolName, Arguments: hexb(p.ArgumentsHex), SideEffecting: p.SideEffecting}}
	case "tool_result":
		f.Payload = &acpv1.Frame_ToolResult{ToolResult: &acpv1.ToolResult{
			CallId: p.CallID, Result: hexb(p.ResultHex), IsError: p.IsError, Mocked: p.Mocked}}
	case "control":
		f.Payload = &acpv1.Frame_Control{Control: &acpv1.Control{
			Kind: acpv1.ControlKind(acpv1.ControlKind_value[p.ControlKind]), Reason: p.Reason,
			AckSeq: u64(p.AckSeq), Credits: u32(p.Credits)}}
	case "turn_end":
		f.Payload = &acpv1.Frame_TurnEnd{TurnEnd: &acpv1.TurnEnd{
			TurnId: p.TurnID, Reason: acpv1.FinishReason(acpv1.FinishReason_value[p.Reason]),
			PromptTokens: u32(p.PromptTokens), CompletionTokens: u32(p.CompletionTokens)}}
	case "binary":
		f.Payload = &acpv1.Frame_Binary{Binary: &acpv1.BinaryChunk{
			Modality: acpv1.Modality(acpv1.Modality_value[p.Modality]), Codec: p.Codec,
			GroupId: u64(p.GroupID), Index: u32(p.Index), Last: p.Last, Data: hexb(p.DataHex),
			Width: u32(p.Width), Height: u32(p.Height), PtsNanos: u64(p.PtsNanos)}}
	case "approval_req":
		f.Payload = &acpv1.Frame_ApprovalReq{ApprovalReq: &acpv1.ApprovalRequest{
			ApprovalId: p.ApprovalID, ActionSummary: p.ActionSummary,
			ActionPayload: hexb(p.ActionPayloadHex), ExpiresAtUnix: i64(p.ExpiresAtUnix),
			Risk: acpv1.RiskLevel(acpv1.RiskLevel_value[p.Risk])}}
	default:
		return nil, fmt.Errorf("unknown payload kind: %q", p.Kind)
	}
	if len(s.Attributes) > 0 {
		f.Attributes = s.Attributes
	}
	return f, nil
}

// HeaderFrame builds a Frame with only the frozen header (fields 1-4). Its marshal IS
// byte-identical to Python's (no oneof/map to reorder) — the contract header-only forwarding needs.
func HeaderFrame(s Spec) *acpv1.Frame {
	return &acpv1.Frame{
		SessionId: s.SessionID, StreamId: u64(s.StreamID), Seq: u64(s.Seq),
		Direction: acpv1.Direction(acpv1.Direction_value[s.Direction]),
	}
}

// MarshalHex deterministically marshals a Frame to a hex string (matches Python
// SerializeToString(deterministic=True).hex()).
func MarshalHex(f *acpv1.Frame) (string, error) {
	b, err := proto.MarshalOptions{Deterministic: true}.Marshal(f)
	if err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// DefaultFixturePath locates tests/fixtures/conformance_frames.json relative to this source file,
// so it resolves identically from `go test` (CWD = package dir) and `go run ./cmd/genfixtures`.
func DefaultFixturePath() string {
	_, thisFile, _, _ := runtime.Caller(0) // .../gateway_core/internal/gateway/conformance.go
	root := filepath.Join(filepath.Dir(thisFile), "..", "..", "..", "..")
	return filepath.Join(root, "tests", "fixtures", "conformance_frames.json")
}

// LoadSpecs reads and decodes the fixture (UseNumber preserves the >2^53 ts_unix_nanos value).
func LoadSpecs(path string) ([]Spec, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var doc struct {
		Frames []Spec `json:"frames"`
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(&doc); err != nil {
		return nil, err
	}
	return doc.Frames, nil
}

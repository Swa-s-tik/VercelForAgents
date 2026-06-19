package gateway

import (
	"fmt"

	"google.golang.org/grpc/encoding"
	"google.golang.org/protobuf/proto"

	"github.com/agentctl/gateway_core/internal/wire"
)

// rawCodec is a drop-in replacement for grpc's default "proto" codec that adds one behavior:
// a *wire.RawFrame is passed through as opaque bytes (no marshal/unmarshal). Every other message
// type is delegated to protobuf exactly as the stock codec does, so registering this as "proto"
// is behavior-preserving for all typed RPCs (Health, the control plane) - only the zero-copy
// Converse path, which uses *wire.RawFrame, takes the fast lane.
//
// grpc v1.64 uses the V1 encoding.Codec interface and selects a codec by content-subtype ("proto"),
// so overriding that name swaps the behavior process-wide without touching call sites. It is
// installed only when the zero-copy fast path is enabled (see EnableZeroCopy), keeping the default
// data plane byte-for-byte unchanged.
type rawCodec struct{}

func (rawCodec) Name() string { return "proto" }

func (rawCodec) Marshal(v any) ([]byte, error) {
	if rf, ok := v.(*wire.RawFrame); ok {
		return rf.B, nil
	}
	m, ok := v.(proto.Message)
	if !ok {
		return nil, fmt.Errorf("rawCodec: cannot marshal %T", v)
	}
	return proto.Marshal(m)
}

func (rawCodec) Unmarshal(data []byte, v any) error {
	if rf, ok := v.(*wire.RawFrame); ok {
		// grpc reuses/recycles the data buffer after the call returns, so copy it: the proxy holds
		// frames across goroutine hops (shadow pipes) past this call's lifetime.
		rf.B = append(rf.B[:0], data...)
		return nil
	}
	m, ok := v.(proto.Message)
	if !ok {
		return fmt.Errorf("rawCodec: cannot unmarshal into %T", v)
	}
	return proto.Unmarshal(data, m)
}

// zeroCopyEnabled is flipped by EnableZeroCopy at startup; the raw ServiceDesc is only registered
// when it is true.
var zeroCopyEnabled bool

// EnableZeroCopy installs the passthrough codec process-wide and arms the raw Converse fast path.
// Call once at startup before serving. Idempotent.
func EnableZeroCopy() {
	if zeroCopyEnabled {
		return
	}
	encoding.RegisterCodec(rawCodec{})
	zeroCopyEnabled = true
}

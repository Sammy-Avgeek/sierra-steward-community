package main

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestDeclineDoesNotInstall(t *testing.T) {
	local := t.TempDir()
	_, attempt := installAfterAgreement(false, local, func(int, string) {
		t.Fatal("an unaccepted agreement should not start installation")
	})
	files, err := os.ReadDir(local)
	if !errors.Is(attempt, errAgreementRequired) || err != nil || len(files) != 0 {
		t.Fatalf("decline should exit before installation: files=%v err=%v", files, err)
	}
}

func TestEmbeddedArchiveChecksPayload(t *testing.T) {
	var payload bytes.Buffer
	archive := zip.NewWriter(&payload)
	entry, err := archive.Create("runtime/electron.exe")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := entry.Write([]byte("runtime")); err != nil {
		t.Fatal(err)
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	previous := payloadSHA
	t.Cleanup(func() { payloadSHA = previous })
	payloadSHA = fmt.Sprintf("%x", sha256.Sum256(payload.Bytes()))
	var footer [8]byte
	binary.LittleEndian.PutUint64(footer[:], uint64(payload.Len()))
	bundle := append([]byte("test executable"), payload.Bytes()...)
	bundle = append(append(bundle, footer[:]...), []byte(payloadMarker)...)
	for _, damaged := range []bool{false, true} {
		if damaged {
			bundle[len("test executable")] ^= 1
		}
		path := filepath.Join(t.TempDir(), "Installer.exe")
		if err := os.WriteFile(path, bundle, 0600); err != nil {
			t.Fatal(err)
		}
		file, err := os.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		found, err := embeddedArchive(file)
		file.Close()
		if damaged && (err == nil || found != nil) {
			t.Fatal("accepted a damaged embedded payload")
		}
		if !damaged && (err != nil || found == nil || len(found.File) != 1) {
			t.Fatalf("valid embedded payload failed: %v", err)
		}
	}
}

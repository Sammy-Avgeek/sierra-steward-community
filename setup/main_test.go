package main

import (
	"errors"
	"os"
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

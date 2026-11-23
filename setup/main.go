package main

import (
	"archive/zip"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"unsafe"
)

//go:embed TERMS.txt
var terms string

var (
	product       = "Sierra Steward Community"
	appExecutable = "SierraStewardCommunity.exe"
	payloadName   = "SierraStewardCommunity.app.zip"
	payloadSHA    string
	messageBox    = syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW")
)

func dialog(text string, flags uintptr) uintptr {
	caption, _ := syscall.UTF16PtrFromString(product + " Setup")
	body, _ := syscall.UTF16PtrFromString(text)
	result, _, _ := messageBox.Call(0, uintptr(unsafe.Pointer(body)), uintptr(unsafe.Pointer(caption)), flags)
	return result
}

func openPayload() (*zip.ReadCloser, error) {
	self, err := os.Executable()
	if err != nil {
		return nil, err
	}
	name := filepath.Join(filepath.Dir(self), payloadName)
	file, err := os.Open(name)
	if err != nil {
		return nil, fmt.Errorf("extract the full setup ZIP and keep %s beside Setup: %w", payloadName, err)
	}
	digest := sha256.New()
	_, err = io.Copy(digest, file)
	file.Close()
	if err != nil {
		return nil, err
	}
	if hex.EncodeToString(digest.Sum(nil)) != payloadSHA {
		return nil, errors.New("the installation archive failed its checksum")
	}
	return zip.OpenReader(name)
}

type progressUpdate func(percent int, detail string)

func unpack(archive *zip.ReadCloser, root string, progress progressUpdate) error {
	percent := 0
	for index, item := range archive.File {
		next := 24 + index*59/len(archive.File)
		if next != percent {
			percent = next
			progress(percent, "Extracting the dashboard and Discord worker…")
		}
		name := strings.TrimSuffix(item.Name, "/")
		local := filepath.FromSlash(name)
		if !fs.ValidPath(name) || !filepath.IsLocal(local) || strings.Contains(name, "\\") {
			return fmt.Errorf("invalid archive filename %q", item.Name)
		}
		target := filepath.Join(root, local)
		if item.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0755); err != nil {
				return err
			}
			continue
		}
		if !item.Mode().IsRegular() {
			return fmt.Errorf("unsupported archive entry %q", item.Name)
		}
		if err := os.MkdirAll(filepath.Dir(target), 0755); err != nil {
			return err
		}
		if err := copyEntry(item, target); err != nil {
			return err
		}
	}
	for _, file := range []string{appExecutable, "resources/app/index.html", "resources/SierraWorker.exe"} {
		if _, err := os.Stat(filepath.Join(root, file)); err != nil {
			return fmt.Errorf("missing setup component %s", file)
		}
	}
	return nil
}

func copyEntry(entry *zip.File, target string) error {
	reader, err := entry.Open()
	if err != nil {
		return err
	}
	defer reader.Close()
	file, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(file, reader)
	closeErr := file.Close()
	return errors.Join(copyErr, closeErr)
}

func install(local string, progress progressUpdate) (string, error) {
	progress(8, "Verifying the application archive…")
	archive, err := openPayload()
	if err != nil {
		return "", err
	}
	defer archive.Close()
	stage, err := os.MkdirTemp(local, "SierraSetup-")
	if err != nil {
		return "", err
	}
	keepStage := false
	defer func() {
		if !keepStage {
			os.RemoveAll(stage)
		}
	}()
	stagedApp := filepath.Join(stage, "app")
	if err := unpack(archive, stagedApp, progress); err != nil {
		return "", err
	}
	installed := filepath.Join(local, product)
	progress(86, "Installing the dashboard…")
	previous := filepath.Join(stage, "previous")
	if _, err := os.Stat(installed); err == nil {
		if err := os.Rename(installed, previous); err != nil {
			return "", fmt.Errorf("close the existing dashboard before updating: %w", err)
		}
	} else if !os.IsNotExist(err) {
		return "", err
	}
	if err := os.Rename(stagedApp, installed); err != nil {
		if _, previousErr := os.Stat(previous); previousErr == nil {
			if rollbackErr := os.Rename(previous, installed); rollbackErr != nil {
				// Keep the previous version if Windows prevents rollback.
				keepStage = true
				return "", fmt.Errorf("installation failed (%v), and rollback failed (%v). Previous files: %s", err, rollbackErr, previous)
			}
		}
		return "", err
	}
	return filepath.Join(installed, appExecutable), nil
}

var errAgreementRequired = errors.New("accept the terms to install Sierra Steward")

func installAfterAgreement(accepted bool, local string, progress progressUpdate) (string, error) {
	if !accepted {
		return "", errAgreementRequired
	}
	if !filepath.IsAbs(local) {
		return "", errors.New("Windows could not find your local application folder")
	}
	return install(local, progress)
}

func verifySetup() error {
	local, err := os.MkdirTemp("", "SierraSetupCheck-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(local)
	_, err = install(local, func(int, string) {})
	return err
}

func main() {
	if slices.Contains(os.Args[1:], "--verify") {
		if err := verifySetup(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if err := showWizard(slices.Contains(os.Args[1:], "--start-bot")); err != nil {
		fmt.Fprintln(os.Stderr, err)
		dialog(err.Error(), 0x10)
		os.Exit(1)
	}
}

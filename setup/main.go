package main

import (
	"archive/zip"
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
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
	product       = "Community Console"
	edition       = "community"
	appExecutable = "CommunityConsole.exe"
	payloadName   = "CommunityConsole.app.zip"
	payloadSHA    string
	messageBox    = syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW")
)

func dialog(text string, flags uintptr) uintptr {
	caption, _ := syscall.UTF16PtrFromString(product + " Setup")
	body, _ := syscall.UTF16PtrFromString(text)
	result, _, _ := messageBox.Call(0, uintptr(unsafe.Pointer(body)), uintptr(unsafe.Pointer(caption)), flags)
	return result
}

const payloadMarker = "SIERRA-APP-V1\x00"

type payloadArchive struct {
	*zip.Reader
	file *os.File
}

func openPayload() (*payloadArchive, error) {
	self, err := os.Executable()
	if err != nil {
		return nil, err
	}
	file, err := os.Open(self)
	if err != nil {
		return nil, err
	}
	archive, err := embeddedArchive(file)
	if err != nil || archive != nil {
		if err != nil {
			file.Close()
		}
		return archive, err
	}
	file.Close()

	// The personal edition also supports the original two-file setup package.
	name := filepath.Join(filepath.Dir(self), payloadName)
	file, err = os.Open(name)
	if err != nil {
		return nil, fmt.Errorf("installation archive unavailable. Download the complete installer again: %w", err)
	}
	info, err := file.Stat()
	if err != nil {
		file.Close()
		return nil, err
	}
	archive, err = checkedArchive(file, io.NewSectionReader(file, 0, info.Size()))
	if err != nil {
		file.Close()
	}
	return archive, err
}

func embeddedArchive(file *os.File) (*payloadArchive, error) {
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	var footer [8 + len(payloadMarker)]byte
	end := info.Size() - int64(len(footer))
	if end < 0 {
		return nil, nil
	}
	if _, err := file.ReadAt(footer[:], end); err != nil {
		return nil, err
	}
	if string(footer[8:]) != payloadMarker {
		return nil, nil
	}
	length := binary.LittleEndian.Uint64(footer[:8])
	if length < 22 || length >= uint64(end) {
		return nil, errors.New("the bundled installation archive has an invalid length")
	}
	return checkedArchive(file, io.NewSectionReader(file, end-int64(length), int64(length)))
}

func checkedArchive(file *os.File, stream *io.SectionReader) (*payloadArchive, error) {
	digest := sha256.New()
	if _, err := io.Copy(digest, stream); err != nil {
		return nil, err
	}
	if hex.EncodeToString(digest.Sum(nil)) != payloadSHA {
		return nil, errors.New("the installation archive failed its checksum")
	}
	reader, err := zip.NewReader(stream, stream.Size())
	if err != nil {
		return nil, err
	}
	return &payloadArchive{Reader: reader, file: file}, nil
}

type progressUpdate func(percent int, detail string)

func unpack(archive *zip.Reader, root string, progress progressUpdate) error {
	percent := 0
	for index, item := range archive.File {
		next := 24 + index*59/len(archive.File)
		if next != percent {
			percent = next
			progress(percent, "Extracting application components…")
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
	return nil
}

func buildDashboard(parts, destination string, bundled bool) error {
	if !bundled {
		return os.Rename(parts, destination)
	}
	if err := os.Rename(filepath.Join(parts, "runtime"), destination); err != nil {
		return err
	}
	for _, move := range [][2]string{
		{filepath.Join(destination, "electron.exe"), filepath.Join(destination, appExecutable)},
		{filepath.Join(parts, "dashboard"), filepath.Join(destination, "resources", "app")},
		{filepath.Join(parts, "worker", "SierraWorker.exe"), filepath.Join(destination, "resources", "SierraWorker.exe")},
		{filepath.Join(parts, "notices", "THIRD-PARTY-NOTICES.txt"), filepath.Join(destination, "THIRD-PARTY-NOTICES.txt")},
	} {
		if err := os.Rename(move[0], move[1]); err != nil {
			return err
		}
	}
	return nil
}

func verifyDashboard(root string) error {
	for _, file := range []string{appExecutable, "resources/app/index.html", "resources/SierraWorker.exe"} {
		if info, err := os.Stat(filepath.Join(root, file)); err != nil || !info.Mode().IsRegular() {
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
	defer archive.file.Close()
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
	parts := filepath.Join(stage, "parts")
	if err := unpack(archive.Reader, parts, progress); err != nil {
		return "", err
	}
	stagedApp := filepath.Join(stage, "app")
	progress(84, "Building the Electron dashboard…")
	componentLayout := slices.ContainsFunc(archive.File, func(file *zip.File) bool { return file.Name == "runtime/electron.exe" })
	if err := buildDashboard(parts, stagedApp, componentLayout); err != nil {
		return "", fmt.Errorf("could not build the Electron dashboard: %w", err)
	}
	if err := verifyDashboard(stagedApp); err != nil {
		return "", err
	}
	installed := filepath.Join(local, product)
	progress(90, "Installing the dashboard…")
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

var errAgreementRequired = errors.New("accept the terms before installing")

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

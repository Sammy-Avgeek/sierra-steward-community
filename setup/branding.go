package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"unicode/utf16"
	"unsafe"
)

const maxLogoBytes = 1_250_000
const legacyCommunityFolder = "Sierra Steward Community"

type botBrand struct {
	Name string `json:"name"`
	Logo string `json:"logo"`
}

func validBotName(name string) bool {
	length := len(utf16.Encode([]rune(strings.TrimSpace(name))))
	return length >= 2 && length <= 32
}

func imageLogo(data []byte) (string, image.Image, error) {
	if len(data) > maxLogoBytes {
		return "", nil, errors.New("Choose a PNG or JPEG image up to 1.25 MB.")
	}
	info, format, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil || (format != "png" && format != "jpeg") || info.Width < 1 || info.Height < 1 || info.Width > 4096 || info.Height > 4096 {
		return "", nil, errors.New("Choose a valid PNG or JPEG up to 4,096 pixels on each side.")
	}
	preview, _, err := image.Decode(bytes.NewReader(data))
	if err != nil {
		return "", nil, errors.New("This image could not be read. Choose another file.")
	}
	return "data:image/" + format + ";base64," + base64.StdEncoding.EncodeToString(data), preview, nil
}

func savedDirectory() (string, error) {
	root := os.Getenv("APPDATA")
	if !filepath.IsAbs(root) {
		return "", errors.New("Windows could not find the settings folder")
	}
	fresh := filepath.Join(root, product)
	legacy := filepath.Join(root, legacyCommunityFolder)
	if !hasSetupData(fresh) && hasSetupData(legacy) {
		return legacy, nil
	}
	return fresh, nil
}

func hasSetupData(directory string) bool {
	for _, name := range []string{".env", "branding.json", "data"} {
		if _, err := os.Stat(filepath.Join(directory, name)); err == nil {
			return true
		}
	}
	return false
}

func existingBrand() (botBrand, image.Image) {
	var brand botBrand
	dir, err := savedDirectory()
	if err != nil {
		return brand, nil
	}
	file, err := os.Open(filepath.Join(dir, "branding.json"))
	if err != nil {
		return brand, nil
	}
	defer file.Close()
	if json.NewDecoder(io.LimitReader(file, maxLogoBytes*2)).Decode(&brand) != nil || !validBotName(brand.Name) {
		return botBrand{}, nil
	}
	if brand.Name == legacyCommunityFolder {
		brand.Name = product
	}
	if header, encoded, ok := strings.Cut(brand.Logo, ","); ok && strings.HasPrefix(header, "data:image/") {
		if raw, err := base64.StdEncoding.DecodeString(encoded); err == nil {
			if logo, preview, err := imageLogo(raw); err == nil {
				brand.Logo = logo
				return brand, preview
			}
		}
	}
	brand.Logo = ""
	return brand, nil
}

func saveBranding(brand botBrand) error {
	dir, err := savedDirectory()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(dir, ".branding-*.json")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	err = errors.Join(json.NewEncoder(file).Encode(brand), file.Close())
	if err != nil {
		return err
	}
	return os.Rename(file.Name(), filepath.Join(dir, "branding.json"))
}

type openFileName struct {
	size                   uint32
	owner, instance        uintptr
	filter, customFilter   *uint16
	maxCustom, filterIndex uint32
	file                   *uint16
	maxFile                uint32
	fileTitle              *uint16
	maxFileTitle           uint32
	initialDir, title      *uint16
	flags                  uint32
	fileOffset, extension  uint16
	defaultExtension       *uint16
	customData, hook       uintptr
	templateName           *uint16
	reserved               uintptr
	reservedSize, flagsEx  uint32
}

func chooseImage(window uintptr) (string, error) {
	dialog := syscall.NewLazyDLL("comdlg32.dll")
	filter := utf16.Encode([]rune("PNG and JPEG images\x00*.png;*.jpg;*.jpeg\x00\x00"))
	var name [32768]uint16
	info := openFileName{size: uint32(unsafe.Sizeof(openFileName{})), owner: window,
		filter: &filter[0], filterIndex: 1, file: &name[0], maxFile: uint32(len(name)),
		title: wide("Choose a bot image"), flags: 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008}
	selected, _, _ := dialog.NewProc("GetOpenFileNameW").Call(uintptr(unsafe.Pointer(&info)))
	if selected != 0 {
		return syscall.UTF16ToString(name[:]), nil
	}
	if code, _, _ := dialog.NewProc("CommDlgExtendedError").Call(); code != 0 {
		return "", errors.New("Windows could not open the image picker.")
	}
	return "", nil
}

func joinWarnings(warnings []string) string { return strings.Join(warnings, "\n") }

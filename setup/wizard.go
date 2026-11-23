package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"sync"
	"syscall"
	"unsafe"
)

var (
	user32       = syscall.NewLazyDLL("user32.dll")
	gdi32        = syscall.NewLazyDLL("gdi32.dll")
	createWindow = user32.NewProc("CreateWindowExW")
	send         = user32.NewProc("SendMessageW")
	show         = user32.NewProc("ShowWindow")
	enable       = user32.NewProc("EnableWindow")
	text         = user32.NewProc("SetWindowTextW")
	setup        *wizard
)

const (
	agreementID     = 101
	installID       = 102
	exitID          = 103
	progressMessage = 0x8001
	tabStop         = 0x10000
)

type class struct {
	size, style                              uint32
	proc                                     uintptr
	classExtra, windowExtra                  int32
	instance, icon, cursor, background, menu uintptr
	name                                     *uint16
	smallIcon                                uintptr
}

type msg struct {
	window         uintptr
	message        uint32
	wParam, lParam uintptr
	time           uint32
	x, y           int32
	private        uint32
}

type progressState struct {
	percent            int
	detail, executable string
	err                error
	finished           bool
}

type wizard struct {
	window, instance, font, titleFont                           uintptr
	headerBrush, headerColour                                   uintptr
	header                                                      []uintptr
	agreement, desktop, menu, install, exit, bar, title, detail uintptr
	options, progress                                           []uintptr
	local, executable                                           string
	busy, failed, autostart                                     bool
	mutex                                                       sync.Mutex
	state                                                       progressState
}

func wide(value string) *uint16 {
	result, _ := syscall.UTF16PtrFromString(value)
	return result
}

func caption(control uintptr, value string) {
	text.Call(control, uintptr(unsafe.Pointer(wide(value))))
}

func selected(control uintptr) bool {
	value, _, _ := send.Call(control, 0x00F0, 0, 0)
	return value == 1
}

func (w *wizard) control(kind, title string, style uint32, x, y, width, height, id int) uintptr {
	child, _, _ := createWindow.Call(0, uintptr(unsafe.Pointer(wide(kind))), uintptr(unsafe.Pointer(wide(title))),
		uintptr(0x50000000|style), uintptr(x), uintptr(y), uintptr(width), uintptr(height), w.window, uintptr(id), w.instance, 0)
	send.Call(child, 0x0030, w.font, 1)
	return child
}

func (w *wizard) label(value string, x, y, width, height int) uintptr {
	return w.control("STATIC", value, 0, x, y, width, height, 0)
}

func showGroup(controls []uintptr, state uintptr) {
	for _, control := range controls {
		show.Call(control, state)
	}
}

func font(height, weight uintptr) uintptr {
	font, _, _ := gdi32.NewProc("CreateFontW").Call(height, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 0, 0,
		uintptr(unsafe.Pointer(wide("Segoe UI"))))
	return font
}

func (w *wizard) build() {
	w.font, w.titleFont = font(^uintptr(16), 400), font(^uintptr(28), 650)
	w.headerColour = 0x00332D14
	if product == "Sierra Steward" {
		w.headerColour = 0x00452B12
	}
	w.headerBrush, _, _ = gdi32.NewProc("CreateSolidBrush").Call(w.headerColour)
	background := w.label("", 0, 0, 602, 90)
	heading := w.label(product, 24, 16, 540, 42)
	send.Call(heading, 0x0030, w.titleFont, 1)
	summary := "Install the Discord community dashboard"
	if product == "Sierra Steward" {
		summary = "Install the Sierra Originals flight operations dashboard"
	}
	subtitle := w.label(summary, 25, 64, 540, 24)
	w.header = []uintptr{background, heading, subtitle}
	user32.NewProc("SetWindowPos").Call(background, 1, 0, 0, 0, 0, 3)
	termsHeading := w.label("Read the Terms of Use before continuing.", 25, 100, 540, 22)
	termsText := strings.ReplaceAll(strings.TrimSpace(strings.ReplaceAll(terms, "\r\n", "\n")), "\n", "\r\n")
	termsBox := w.control("EDIT", termsText, tabStop|0x00800000|0x00200000|0x0800|0x0040|0x0004, 25, 128, 545, 195, 0)
	w.agreement = w.control("BUTTON", "I agree to the Terms of Use", tabStop|3, 25, 333, 545, 24, agreementID)
	location := w.label("Install to  "+filepath.Join(w.local, product), 25, 369, 545, 38)
	w.desktop = w.control("BUTTON", "Create a desktop shortcut", tabStop|3, 25, 410, 250, 25, 104)
	w.menu = w.control("BUTTON", "Add to the Start menu", tabStop|3, 293, 410, 245, 25, 105)
	send.Call(w.desktop, 0x00F1, 1, 0)
	send.Call(w.menu, 0x00F1, 1, 0)
	w.options = []uintptr{termsHeading, termsBox, w.agreement, location, w.desktop, w.menu}
	w.title = w.label("Installing", 25, 117, 545, 40)
	send.Call(w.title, 0x0030, w.titleFont, 1)
	w.bar = w.control("msctls_progress32", "", 0, 25, 175, 545, 20, 0)
	w.detail = w.label("Preparing installation…", 25, 215, 545, 190)
	w.progress = []uintptr{w.title, w.bar, w.detail}
	showGroup(w.progress, 0)
	w.exit = w.control("BUTTON", "Cancel", tabStop, 305, 471, 105, 32, exitID)
	w.install = w.control("BUTTON", "Install", tabStop|1, 425, 471, 145, 32, installID)
	enable.Call(w.install, 0)
}

func (w *wizard) post(state progressState) {
	w.mutex.Lock()
	w.state = state
	w.mutex.Unlock()
	user32.NewProc("PostMessageW").Call(w.window, progressMessage, 0, 0)
}

func (w *wizard) begin() {
	if !selected(w.agreement) {
		return
	}
	desktop, menu := selected(w.desktop), selected(w.menu)
	w.busy = true
	showGroup(w.options, 0)
	showGroup(w.progress, 5)
	caption(w.title, "Installing "+product)
	enable.Call(w.install, 0)
	enable.Call(w.exit, 0)
	go func() {
		executable, err := installAfterAgreement(true, w.local, func(percent int, detail string) {
			w.post(progressState{percent: percent, detail: detail})
		})
		detail := "Setup is complete. Open the dashboard to get started."
		if err == nil && (desktop || menu) {
			w.post(progressState{percent: 95, detail: "Creating Windows shortcuts…"})
			if createShortcuts(executable, desktop, menu) != nil {
				detail += " Windows could not create the selected shortcuts. The dashboard is installed."
			}
		}
		w.post(progressState{percent: 100, detail: detail, executable: executable, err: err, finished: true})
	}()
}

func (w *wizard) refresh() {
	if !w.busy {
		return
	}
	w.mutex.Lock()
	state := w.state
	w.mutex.Unlock()
	send.Call(w.bar, 0x0402, uintptr(state.percent), 0)
	caption(w.detail, state.detail)
	if !state.finished {
		return
	}
	w.busy, w.failed, w.executable = false, state.err != nil, state.executable
	if w.failed {
		caption(w.title, "Setup needs attention")
		caption(w.detail, state.err.Error())
		caption(w.install, "Back")
		fmt.Fprintln(os.Stderr, state.err)
	} else {
		caption(w.title, "Ready to open")
		caption(w.install, "Open dashboard")
	}
	caption(w.exit, "Close")
	enable.Call(w.install, 1)
	enable.Call(w.exit, 1)
}

func (w *wizard) open() error {
	var args []string
	if w.autostart {
		args = append(args, "--autostart")
	}
	command := exec.Command(w.executable, args...)
	command.Dir = filepath.Dir(w.executable)
	return command.Start()
}

func handleWindow(window uintptr, message uint32, wParam, lParam uintptr) uintptr {
	w := setup
	switch message {
	case 0x0138:
		if w != nil && slices.Contains(w.header, lParam) {
			gdi32.NewProc("SetTextColor").Call(wParam, 0x00FFF9EF)
			gdi32.NewProc("SetBkColor").Call(wParam, w.headerColour)
			return w.headerBrush
		}
	case 0x0111:
		switch wParam & 0xffff {
		case agreementID:
			var state uintptr
			if selected(w.agreement) {
				state = 1
			}
			enable.Call(w.install, state)
		case installID:
			if w.busy {
				return 0
			}
			if w.failed {
				w.failed = false
				showGroup(w.progress, 0)
				showGroup(w.options, 5)
				caption(w.install, "Install")
				caption(w.exit, "Cancel")
			} else if w.executable != "" {
				if err := w.open(); err != nil {
					dialog(err.Error(), 0x10)
				} else {
					user32.NewProc("DestroyWindow").Call(window)
				}
			} else {
				w.begin()
			}
		case exitID, 2:
			if !w.busy {
				user32.NewProc("DestroyWindow").Call(window)
			}
		}
		return 0
	case progressMessage:
		w.refresh()
		return 0
	case 0x0010:
		if w.busy {
			return 0
		}
	case 0x0002:
		user32.NewProc("PostQuitMessage").Call(0)
		return 0
	}
	result, _, _ := user32.NewProc("DefWindowProcW").Call(window, uintptr(message), wParam, lParam)
	return result
}

func showWizard(autostart bool) error {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	syscall.NewLazyDLL("comctl32.dll").NewProc("InitCommonControls").Call()
	instance, _, _ := syscall.NewLazyDLL("kernel32.dll").NewProc("GetModuleHandleW").Call(0)
	cursor, _, _ := user32.NewProc("LoadCursorW").Call(0, 32512)
	className := wide("SierraStewardSetupWizard")
	registration := class{size: uint32(unsafe.Sizeof(class{})), proc: syscall.NewCallback(handleWindow),
		instance: instance, cursor: cursor, background: 6, name: className}
	if atom, _, err := user32.NewProc("RegisterClassExW").Call(uintptr(unsafe.Pointer(&registration))); atom == 0 {
		return err
	}
	setup = &wizard{instance: instance, local: os.Getenv("LOCALAPPDATA"), autostart: autostart}
	if !filepath.IsAbs(setup.local) {
		return errors.New("Windows could not find your local application folder")
	}
	window, _, err := createWindow.Call(0, uintptr(unsafe.Pointer(className)), uintptr(unsafe.Pointer(wide(product+" Setup"))),
		0x00CA0000, 0x80000000, 0x80000000, 618, 559, 0, 0, instance, 0)
	if window == 0 {
		return err
	}
	setup.window = window
	setup.build()
	show.Call(window, 5)
	user32.NewProc("SetForegroundWindow").Call(window)
	var event msg
	get := user32.NewProc("GetMessageW")
	dialogKey := user32.NewProc("IsDialogMessageW")
	translate := user32.NewProc("TranslateMessage")
	dispatch := user32.NewProc("DispatchMessageW")
	for {
		ret, _, err := get.Call(uintptr(unsafe.Pointer(&event)), 0, 0, 0)
		if int32(ret) == -1 {
			return err
		}
		if ret == 0 {
			return nil
		}
		if handled, _, _ := dialogKey.Call(window, uintptr(unsafe.Pointer(&event))); handled == 0 {
			translate.Call(uintptr(unsafe.Pointer(&event)))
			dispatch.Call(uintptr(unsafe.Pointer(&event)))
		}
	}
}

func createShortcuts(executable string, desktop, startMenu bool) error {
	const script = `
$ErrorActionPreference = 'Stop'
$wsh = New-Object -ComObject WScript.Shell
foreach ($folder in @('DesktopDirectory','Programs')) {
    if ($folder -eq 'DesktopDirectory' -and $env:SIERRA_SETUP_DESKTOP -ne 'true') { continue }
    if ($folder -eq 'Programs' -and $env:SIERRA_SETUP_MENU -ne 'true') { continue }
    $path = [Environment]::GetFolderPath($folder)
    if (-not $path) { throw 'Windows did not provide the shortcut folder.' }
    $link = $wsh.CreateShortcut((Join-Path $path ($env:SIERRA_SETUP_NAME + '.lnk')))
    $link.TargetPath = $env:SIERRA_SETUP_APP
    $link.WorkingDirectory = Split-Path -LiteralPath $env:SIERRA_SETUP_APP
    $link.Save()
}`
	powershell := filepath.Join(os.Getenv("SystemRoot"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
	command := exec.Command(powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script)
	command.Env = append(os.Environ(), "SIERRA_SETUP_APP="+executable, "SIERRA_SETUP_NAME="+product,
		fmt.Sprintf("SIERRA_SETUP_DESKTOP=%t", desktop), fmt.Sprintf("SIERRA_SETUP_MENU=%t", startMenu))
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	return command.Run()
}

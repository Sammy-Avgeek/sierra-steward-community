package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"time"
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
	verification       bool
}

type wizard struct {
	window, instance  uintptr
	local, executable string
	busy, autostart   bool
	mutex             sync.Mutex
	state             progressState
	ui                wizardUI
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

func (w *wizard) post(state progressState) {
	w.mutex.Lock()
	w.state = state
	w.mutex.Unlock()
	user32.NewProc("PostMessageW").Call(w.window, progressMessage, 0, 0)
}

func (w *wizard) begin() {
	if !selected(w.ui.agreement) || (edition == "community" && !w.brandReady()) {
		return
	}
	desktop, menu := selected(w.ui.desktop), selected(w.ui.menu)
	w.ui.shortcutDesktop, w.ui.shortcutMenu = desktop, menu
	w.busy = true
	w.ui.percent = 0
	w.showPage(pageWork)
	go func() {
		executable, err := installAfterAgreement(true, w.local, func(percent int, detail string) {
			w.post(progressState{percent: percent, detail: detail})
		})
		var warnings []string
		if err == nil && edition == "personal" && (desktop || menu) {
			w.post(progressState{percent: 95, detail: "Creating Windows shortcuts…"})
			if createShortcuts(executable, desktop, menu) != nil {
				warnings = append(warnings, "Windows could not create one or more of the selected shortcuts.")
			}
		}
		w.post(progressState{percent: 100, detail: joinWarnings(warnings), executable: executable, err: err, finished: true})
	}()
}

func (w *wizard) refresh() {
	if !w.busy {
		return
	}
	w.mutex.Lock()
	state := w.state
	w.mutex.Unlock()
	if w.ui.page == pageConnect {
		if !state.verification {
			return
		}
		w.busy = false
		if state.err != nil {
			caption(w.ui.connectStatus, state.err.Error())
			w.syncButtons()
			return
		}
		caption(w.ui.token, "")
		caption(w.ui.readyNote, state.detail)
		w.showPage(pageDone)
		return
	}
	w.ui.percent = state.percent
	if !state.finished {
		caption(w.ui.workDetail, state.detail)
		caption(w.ui.workPercent, fmt.Sprintf("%d%%", state.percent))
		w.repaint()
		return
	}
	w.busy, w.executable = false, state.executable
	if state.err != nil {
		caption(w.ui.errorDetail, state.err.Error())
		w.showPage(pageError)
		fmt.Fprintln(os.Stderr, state.err)
	} else {
		if edition == "community" {
			w.showPage(pageConnect)
		} else {
			caption(w.ui.readyNote, state.detail)
			w.showPage(pageDone)
		}
	}
}

func (w *wizard) openPortal() error {
	process := exec.Command(w.executable, "--setup-portal")
	process.Dir = filepath.Dir(w.executable)
	if err := process.Start(); err != nil {
		return err
	}
	return process.Process.Release()
}

func (w *wizard) openPortalInBrowser() bool {
	shellExecute := syscall.NewLazyDLL("shell32.dll").NewProc("ShellExecuteW")
	result, _, _ := shellExecute.Call(w.window, uintptr(unsafe.Pointer(wide("open"))),
		uintptr(unsafe.Pointer(wide("https://discord.com/developers/applications"))), 0, 0, 1)
	return result > 32
}

func (w *wizard) connectBot(useSaved bool) {
	token := strings.TrimSpace(nativeText(w.ui.token))
	if (!useSaved && token == "") || w.busy {
		return
	}
	dir, err := savedDirectory()
	if err != nil {
		caption(w.ui.connectStatus, err.Error())
		return
	}
	brand := w.ui.brand
	desktop, menu := w.ui.shortcutDesktop, w.ui.shortcutMenu
	w.busy = true
	w.syncButtons()
	caption(w.ui.connectStatus, "Verifying the bot with Discord…")
	go func() {
		detail, err := verifyBot(filepath.Join(filepath.Dir(w.executable), "resources", "SierraWorker.exe"), dir, token, brand, useSaved)
		if err == nil {
			if saveBranding(brand) != nil {
				detail += " The dashboard could not save your bot name and image locally."
			}
			if (desktop || menu) && createShortcuts(w.executable, desktop, menu) != nil {
				detail += " Windows could not create one or more of the selected shortcuts."
			}
		}
		w.post(progressState{detail: detail, err: err, finished: true, verification: true})
	}()
}

func verifyBot(worker, directory, token string, brand botBrand, useSaved bool) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 55*time.Second)
	defer cancel()
	request, err := json.Marshal(map[string]any{"token": token, "name": brand.Name, "logo": brand.Logo, "use_saved": useSaved})
	if err != nil {
		return "", err
	}
	process := exec.CommandContext(ctx, worker, "provision-bot")
	process.Stdin = bytes.NewReader(request)
	process.Env = append(os.Environ(), "SIERRA_CONFIG_DIR="+directory, "PYTHONUTF8=1")
	process.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	response, err := process.Output()
	if err != nil {
		return "", errors.New("Could not verify the bot. Check the Discord connection and try again.")
	}
	var result struct {
		Value struct {
			ID      string
			Message string
		}
		Error string
	}
	if json.Unmarshal(response, &result) != nil || (result.Error == "" && result.Value.ID == "") {
		return "", errors.New("Discord verification returned an incomplete result. Please try again.")
	}
	if result.Error != "" {
		return "", errors.New(result.Error)
	}
	return result.Value.Message, nil
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
	case 0x000F:
		if w.ui.ready {
			w.paint()
			return 0
		}
	case 0x0014:
		if w.ui.ready {
			return 1
		}
	case 0x0133, 0x0135, 0x0138:
		if style, ok := w.ui.labels[lParam]; ok {
			return w.colourControl(wParam, style)
		}
	case 0x002B:
		if lParam != 0 && w.drawButton((*drawItem)(unsafe.Pointer(lParam))) {
			return 1
		}
	case 0x0111:
		if w.ui.ready {
			w.command(wParam)
			return 0
		}
	case progressMessage:
		w.refresh()
		return 0
	case 0x0010:
		if w.busy {
			return 0
		}
	case 0x0002:
		w.ui.release()
		user32.NewProc("PostQuitMessage").Call(0)
		return 0
	}
	result, _, _ := user32.NewProc("DefWindowProcW").Call(window, uintptr(message), wParam, lParam)
	return result
}

func showWizard(autostart bool) error {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	user32.NewProc("SetProcessDPIAware").Call()
	instance, _, _ := syscall.NewLazyDLL("kernel32.dll").NewProc("GetModuleHandleW").Call(0)
	cursor, _, _ := user32.NewProc("LoadCursorW").Call(0, 32512)
	className := wide("DiscordDashboardSetup")
	registration := class{size: uint32(unsafe.Sizeof(class{})), proc: syscall.NewCallback(handleWindow),
		instance: instance, cursor: cursor, background: 6, name: className}
	if atom, _, err := user32.NewProc("RegisterClassExW").Call(uintptr(unsafe.Pointer(&registration))); atom == 0 {
		return err
	}
	setup = &wizard{instance: instance, local: os.Getenv("LOCALAPPDATA"), autostart: autostart}
	if !filepath.IsAbs(setup.local) {
		return errors.New("Windows could not find your local application folder")
	}
	x, y, width, height := setup.measure()
	window, _, err := createWindow.Call(0, uintptr(unsafe.Pointer(className)), uintptr(unsafe.Pointer(wide(product+" Setup"))),
		windowStyle, uintptr(x), uintptr(y), uintptr(width), uintptr(height), 0, 0, instance, 0)
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

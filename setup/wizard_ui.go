package main

import (
	"bytes"
	_ "embed"
	"image"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

//go:embed mark.png
var installerMark []byte

const (
	clientWidth, clientHeight                                                 = 884, 588
	windowStyle                                                               = 0x02CA0000 // Caption, system menu, and a fixed frame.
	nameID, imageID, defaultID, portalID, tokenID, backID, savedID, browserID = 201, 202, 203, 204, 205, 206, 207, 208
)

type page int

const (
	pageBrand page = iota
	pageReview
	pageWork
	pageConnect
	pageDone
	pageError
	pageCount
)

type color uintptr

func rgb(r, g, b uint8) color { return color(r) | color(g)<<8 | color(b)<<16 }

type palette struct{ sidebar, selected, sidebarText, sea, deep, canvas, panel, edge, ink, muted color }

func theme() palette {
	p := palette{rgb(11, 37, 34), rgb(23, 63, 56), rgb(166, 195, 185), rgb(105, 232, 194), rgb(6, 111, 88),
		rgb(243, 248, 245), rgb(255, 255, 255), rgb(210, 225, 218), rgb(23, 46, 39), rgb(69, 99, 87)}
	if edition == "personal" {
		p.sidebar, p.selected, p.sidebarText, p.sea, p.deep = rgb(15, 31, 53), rgb(32, 58, 88), rgb(179, 202, 227), rgb(125, 205, 255), rgb(32, 94, 160)
		p.canvas, p.edge, p.ink, p.muted = rgb(244, 247, 252), rgb(215, 225, 239), rgb(22, 40, 66), rgb(66, 88, 119)
	}
	return p
}

type rect struct{ l, t, r, b int32 }
type controlColor struct{ bg, fg color }
type drawItem struct {
	controlType, id, itemID, action, state uint32
	window, dc                             uintptr
	bounds                                 rect
	data                                   uintptr
}
type paintStruct struct {
	dc              uintptr
	erase           int32
	bounds          rect
	restore, update int32
	reserved        [32]byte
}
type bitmap struct {
	width, height int
	pixels        []byte
}
type bitmapHeader struct {
	size                   uint32
	width, height          int32
	planes, bits           uint16
	compression, sizeImage uint32
	xRes, yRes             int32
	used, important        uint32
}

type wizardUI struct {
	ready                                                                        bool
	dpi                                                                          int
	colors                                                                       palette
	brushes                                                                      map[color]uintptr
	labels                                                                       map[uintptr]controlColor
	fonts                                                                        [4]uintptr
	page                                                                         page
	groups                                                                       [pageCount][]uintptr
	steps                                                                        []uintptr
	title, hint, primary, secondary, back                                        uintptr
	name, imageNote, agreement, desktop, menu                                    uintptr
	workDetail, workPercent, token, saved, connectStatus, readyNote, errorDetail uintptr
	brand                                                                        botBrand
	percent                                                                      int
	logo, preview                                                                bitmap
	shortcutDesktop, shortcutMenu                                                bool
}

func (w *wizard) px(n int) int { return (n*w.ui.dpi + 48) / 96 }

func (w *wizard) measure() (int, int, int, int) {
	dpi, _, _ := user32.NewProc("GetDpiForSystem").Call()
	w.ui.dpi = int(dpi)
	if w.ui.dpi == 0 {
		w.ui.dpi = 96
	}
	var work rect
	user32.NewProc("SystemParametersInfoW").Call(0x30, 0, uintptr(unsafe.Pointer(&work)), 0)
	availableW, availableH := int(work.r-work.l), int(work.b-work.t)
	if availableW > 0 && availableH > 0 {
		w.ui.dpi = min(w.ui.dpi, (availableW-12)*96/(clientWidth+20), (availableH-12)*96/(clientHeight+44))
	}
	w.ui.dpi = max(48, w.ui.dpi)
	frame := rect{r: int32(w.px(clientWidth)), b: int32(w.px(clientHeight))}
	user32.NewProc("AdjustWindowRectEx").Call(uintptr(unsafe.Pointer(&frame)), windowStyle, 0, 0)
	width, height := int(frame.r-frame.l), int(frame.b-frame.t)
	return int(work.l) + (availableW-width)/2, int(work.t) + (availableH-height)/2, width, height
}

func (u *wizardUI) brush(c color) uintptr {
	if brush := u.brushes[c]; brush != 0 {
		return brush
	}
	brush, _, _ := gdi32.NewProc("CreateSolidBrush").Call(uintptr(c))
	u.brushes[c] = brush
	return brush
}

func (u *wizardUI) release() {
	for _, font := range u.fonts {
		if font != 0 {
			gdi32.NewProc("DeleteObject").Call(font)
		}
	}
	for _, brush := range u.brushes {
		gdi32.NewProc("DeleteObject").Call(brush)
	}
}

func (w *wizard) add(kind, text string, flags uint32, id, x, y, ww, hh, font int, bg, fg color) uintptr {
	h, _, _ := createWindow.Call(0, uintptr(unsafe.Pointer(wide(kind))), uintptr(unsafe.Pointer(wide(text))),
		uintptr(0x40000000|flags), uintptr(w.px(x)), uintptr(w.px(y)), uintptr(w.px(ww)), uintptr(w.px(hh)), w.window, uintptr(id), w.instance, 0)
	w.ui.labels[h] = controlColor{bg, fg}
	send.Call(h, 0x0030, w.ui.fonts[font], 1)
	return h
}

func (w *wizard) group(p page, controls ...uintptr) {
	w.ui.groups[p] = append(w.ui.groups[p], controls...)
}

func (w *wizard) build() {
	u := &w.ui
	p := theme()
	u.colors, u.brushes, u.labels = p, make(map[color]uintptr), make(map[uintptr]controlColor)
	for i, size := range []int{16, 16, 30, 22} {
		weight := 400
		if i > 0 {
			weight = 600
		}
		u.fonts[i], _, _ = gdi32.NewProc("CreateFontW").Call(uintptr(-w.px(size)), 0, 0, 0, uintptr(weight), 0, 0, 0, 1, 0, 0, 0, 0, uintptr(unsafe.Pointer(wide("Segoe UI"))))
	}
	img, _, _ := image.Decode(bytes.NewReader(installerMark))
	u.logo = previewBitmap(img, p.sidebar)
	label := func(s string, x, y, ww, hh, f int, bg, fg color) uintptr {
		return w.add("STATIC", s, 0, 0, x, y, ww, hh, f, bg, fg)
	}
	button := func(s string, id, x, y, ww int) uintptr {
		return w.add("BUTTON", s, tabStop|0x000B, id, x, y, ww, 38, 1, p.canvas, p.ink)
	}
	check := func(s string, id, x, y, ww int, bg color) uintptr {
		return w.add("BUTTON", s, tabStop|3, id, x, y, ww, 28, 0, bg, p.ink)
	}
	edit := func(s string, flags uint32, id, x, y, ww, hh int) uintptr {
		return w.add("EDIT", s, tabStop|flags, id, x, y, ww, hh, 0, p.panel, p.ink)
	}
	common := []uintptr{label(product, 25, 111, 180, 55, 3, p.sidebar, p.panel)}
	steps := []string{"Bot identity", "Review & install", "Connect to Discord", "Ready to open"}
	if edition == "personal" {
		steps = []string{"Review & install", "Ready to open"}
	}
	for i, entry := range steps {
		u.steps = append(u.steps, label(entry, 48, 213+i*46, 156, 26, 1, p.sidebar, p.sidebarText))
	}
	common = append(common, u.steps...)
	u.title = label("", 250, 32, 603, 44, 2, p.canvas, p.ink)
	u.hint = label("", 251, 80, 603, 43, 0, p.canvas, p.muted)
	showGroup(append(common, u.title, u.hint), 5)
	if edition == "community" {
		brand, img := existingBrand()
		u.brand = brand
		u.preview = previewBitmap(img, p.panel)
		u.name = edit(brand.Name, 0x00800000|0x80, nameID, 269, 171, 559, 31)
		send.Call(u.name, 0x00C5, 32, 0)
		send.Call(u.name, 0x1501, 1, uintptr(unsafe.Pointer(wide("Enter a bot name"))))
		u.imageNote = label("PNG or JPEG · up to 1.25 MB", 393, 333, 426, 50, 0, p.panel, p.muted)
		if brand.Logo != "" {
			caption(u.imageNote, "Saved bot image")
		}
		w.group(pageBrand, label("Bot name", 269, 144, 330, 22, 1, p.panel, p.ink), u.name,
			label("Bot image", 393, 244, 429, 27, 1, p.panel, p.ink), button("Choose image", imageID, 393, 279, 150),
			button("Use included image", defaultID, 553, 279, 187), u.imageNote)
	}
	termsText := strings.ReplaceAll(strings.TrimSpace(strings.ReplaceAll(terms, "\r\n", "\n")), "\n", "\r\n")
	termsBox := edit(termsText, 0x00200000|0x0800|0x0040|0x0004, 0, 268, 135, 561, 167)
	u.agreement = check("I agree to the Terms of Use", agreementID, 255, 326, 580, p.canvas)
	u.desktop = check("Create desktop shortcut", 0, 268, 428, 267, p.panel)
	u.menu = check("Add to Start menu", 0, 549, 428, 270, p.panel)
	send.Call(u.desktop, 0x00F1, 1, 0)
	send.Call(u.menu, 0x00F1, 1, 0)
	w.group(pageReview, termsBox, u.agreement, label("Install location", 268, 365, 415, 22, 1, p.panel, p.ink),
		edit(filepath.Join(w.local, product), 0x0800|0x80, 0, 268, 391, 559, 30), u.desktop, u.menu)
	u.workDetail = label("Preparing the installer…", 269, 170, 480, 48, 1, p.panel, p.ink)
	u.workPercent = label("0%", 776, 170, 55, 25, 1, p.panel, p.deep)
	w.group(pageWork, u.workDetail, u.workPercent)
	u.token = edit("", 0x00800000|0x80|0x20, tokenID, 269, 354, 559, 31)
	send.Call(u.token, 0x00C5, 512, 0)
	u.connectStatus = label("", 269, 403, 559, 52, 0, p.panel, p.muted)
	w.group(pageConnect, label("1. Open the Developer Portal and sign in to Discord.", 269, 149, 559, 26, 1, p.panel, p.ink),
		button("Open Developer Portal", portalID, 269, 183, 215), button("Open in browser", browserID, 494, 183, 158),
		label("2. Create a New Application. In Bot, enable Server Members Intent.", 269, 242, 559, 53, 0, p.panel, p.ink),
		label("3. Copy its bot token, then paste it below.", 269, 302, 559, 26, 1, p.panel, p.ink),
		label("Bot token", 269, 329, 300, 22, 0, p.panel, p.muted), u.token, u.connectStatus)
	u.saved = button("Verify saved bot", savedID, 662, 183, 166)
	u.readyNote = label("", 269, 218, 559, 83, 0, p.panel, p.muted)
	w.group(pageDone, label("Installation complete", 269, 155, 555, 32, 3, p.panel, p.ink), u.readyNote)
	u.errorDetail = label("", 269, 166, 559, 140, 0, p.panel, p.ink)
	w.group(pageError, u.errorDetail)
	u.back = button("Back", backID, 253, 519, 106)
	u.secondary = button("Cancel", exitID, 550, 519, 113)
	u.primary = button("Continue", installID, 677, 519, 159)
	u.ready = true
	if edition == "community" {
		w.showPage(pageBrand)
	} else {
		w.showPage(pageReview)
	}
}

func showGroup(controls []uintptr, state uintptr) {
	for _, h := range controls {
		show.Call(h, state)
	}
}

func (w *wizard) showPage(p page) {
	u := &w.ui
	u.page = p
	for group, controls := range u.groups {
		state := uintptr(0)
		if page(group) == p {
			state = 5
		}
		showGroup(controls, state)
	}
	titles := [pageCount][3]string{
		{"Give your bot an identity", "Enter a name and choose the image it will use in Discord.", "Continue"},
		{"Review & install", "Read the terms and choose where Windows should add shortcuts.", "Install"},
		{"Building the dashboard", "Keep this window open until setup finishes.", "Installing…"},
		{"Connect your Discord bot", "Create the bot and verify its token to finish setup.", "Verify & connect"},
		{"Your dashboard is ready", "Open it to finish connecting the bot to your server.", "Open dashboard"},
		{"Setup couldn’t finish", "You can go back and retry the installation.", "Back to review"},
	}
	if edition == "personal" && p == pageDone {
		titles[p][1] = "Open it to start managing your Discord server."
	}
	caption(u.title, titles[p][0])
	caption(u.hint, titles[p][1])
	caption(u.primary, titles[p][2])
	caption(u.secondary, "Cancel")
	if p == pageDone || p == pageError {
		caption(u.secondary, "Close")
	}
	showGroup([]uintptr{u.primary, u.secondary}, 5)
	show.Call(u.saved, 0)
	if p == pageConnect {
		if dir, err := savedDirectory(); err == nil {
			if _, err := os.Stat(filepath.Join(dir, ".env")); err == nil {
				show.Call(u.saved, 5)
			}
		}
	}
	if edition == "community" && p == pageReview {
		show.Call(u.back, 5)
	} else {
		show.Call(u.back, 0)
	}
	w.syncButtons()
	focus := u.primary
	switch p {
	case pageBrand:
		focus = u.name
	case pageReview:
		focus = u.agreement
	case pageConnect:
		focus = u.token
	case pageWork:
		focus = 0
	}
	if focus != 0 {
		user32.NewProc("SetFocus").Call(focus)
	}
	w.repaint()
}

func nativeText(h uintptr) string {
	n, _, _ := user32.NewProc("GetWindowTextLengthW").Call(h)
	if n == 0 {
		return ""
	}
	text := make([]uint16, int(n)+1)
	user32.NewProc("GetWindowTextW").Call(h, uintptr(unsafe.Pointer(&text[0])), uintptr(len(text)))
	return syscall.UTF16ToString(text)
}

func (w *wizard) brandReady() bool {
	w.ui.brand.Name = strings.TrimSpace(nativeText(w.ui.name))
	return validBotName(w.ui.brand.Name) && w.ui.brand.Logo != ""
}

func (w *wizard) syncButtons() {
	u := &w.ui
	allowed := !w.busy
	switch u.page {
	case pageBrand:
		allowed = allowed && w.brandReady()
	case pageReview:
		allowed = allowed && selected(u.agreement)
	case pageConnect:
		allowed = allowed && strings.TrimSpace(nativeText(u.token)) != ""
	}
	var mainState, exitState uintptr
	if allowed {
		mainState = 1
	}
	if !w.busy {
		exitState = 1
	}
	enable.Call(u.primary, mainState)
	enable.Call(u.secondary, exitState)
	if u.saved != 0 {
		enable.Call(u.saved, exitState)
	}
}

func (w *wizard) setImage(bundled bool) {
	var data []byte
	name := "Included bot image selected"
	var err error
	if bundled {
		data = installerMark
	} else {
		var path string
		path, err = chooseImage(w.window)
		if err == nil && path == "" {
			return
		}
		if err == nil {
			var file *os.File
			file, err = os.Open(path)
			if err == nil {
				data, err = io.ReadAll(io.LimitReader(file, maxLogoBytes+1))
				file.Close()
			}
			name = filepath.Base(path)
		}
	}
	if err == nil {
		var logo string
		var img image.Image
		logo, img, err = imageLogo(data)
		if err == nil {
			w.ui.brand.Logo = logo
			w.ui.preview = previewBitmap(img, w.ui.colors.canvas)
			caption(w.ui.imageNote, name)
		}
	}
	if err != nil {
		caption(w.ui.imageNote, err.Error())
	}
	w.syncButtons()
	w.repaint()
}

func (w *wizard) command(message uintptr) {
	u := &w.ui
	id, notification := message&0xffff, (message>>16)&0xffff
	if (id == nameID || id == tokenID) && notification == 0x300 {
		w.syncButtons()
		return
	}
	if notification != 0 || w.busy {
		return
	}
	switch id {
	case agreementID:
		w.syncButtons()
	case imageID:
		w.setImage(false)
	case defaultID:
		w.setImage(true)
	case portalID:
		if err := w.openPortal(); err != nil {
			caption(u.connectStatus, "Could not open the Developer Portal. Try Open in browser.")
		}
	case browserID:
		if !w.openPortalInBrowser() {
			caption(u.connectStatus, "Could not open your browser. Visit discord.com/developers/applications.")
		}
	case savedID:
		w.connectBot(true)
	case backID:
		w.showPage(pageBrand)
	case exitID, 2:
		user32.NewProc("DestroyWindow").Call(w.window)
	case installID:
		switch u.page {
		case pageBrand:
			if w.brandReady() {
				w.showPage(pageReview)
			}
		case pageReview:
			w.begin()
		case pageConnect:
			w.connectBot(false)
		case pageError:
			w.showPage(pageReview)
		case pageDone:
			if err := w.open(); err != nil {
				dialog(err.Error(), 0x10)
			} else {
				user32.NewProc("DestroyWindow").Call(w.window)
			}
		}
	}
}

func (w *wizard) colourControl(dc uintptr, style controlColor) uintptr {
	gdi32.NewProc("SetTextColor").Call(dc, uintptr(style.fg))
	gdi32.NewProc("SetBkColor").Call(dc, uintptr(style.bg))
	return w.ui.brush(style.bg)
}

func fill(dc uintptr, box rect, brush uintptr) {
	user32.NewProc("FillRect").Call(dc, uintptr(unsafe.Pointer(&box)), brush)
}

func (w *wizard) round(dc uintptr, box rect, radius int, c color) {
	selectObject := gdi32.NewProc("SelectObject")
	pen, _, _ := gdi32.NewProc("GetStockObject").Call(8)
	oldPen, _, _ := selectObject.Call(dc, pen)
	old, _, _ := selectObject.Call(dc, w.ui.brush(c))
	d := uintptr(w.px(radius) * 2)
	gdi32.NewProc("RoundRect").Call(dc, uintptr(box.l), uintptr(box.t), uintptr(box.r), uintptr(box.b), d, d)
	selectObject.Call(dc, old)
	selectObject.Call(dc, oldPen)
}

func (w *wizard) drawButton(item *drawItem) bool {
	if item.controlType != 4 || item.window == 0 {
		return false
	}
	u, p := &w.ui, w.ui.colors
	fill(item.dc, item.bounds, u.brush(u.labels[item.window].bg))
	foreground, background := p.ink, p.edge
	if item.window == u.primary {
		foreground, background = p.panel, p.deep
	}
	if item.state&4 != 0 {
		foreground, background = p.muted, p.edge
	}
	if item.state&1 != 0 && item.window != u.primary {
		background = p.canvas
	}
	w.round(item.dc, item.bounds, 8, background)
	old, _, _ := gdi32.NewProc("SelectObject").Call(item.dc, u.fonts[1])
	defer gdi32.NewProc("SelectObject").Call(item.dc, old)
	gdi32.NewProc("SetTextColor").Call(item.dc, uintptr(foreground))
	gdi32.NewProc("SetBkMode").Call(item.dc, 1)
	label := nativeText(item.window)
	user32.NewProc("DrawTextW").Call(item.dc, uintptr(unsafe.Pointer(wide(label))), ^uintptr(0), uintptr(unsafe.Pointer(&item.bounds)), 0x825)
	if item.state&0x10 != 0 && item.state&0x200 == 0 {
		focus := item.bounds
		inset := int32(w.px(4))
		focus.l += inset
		focus.t += inset
		focus.r -= inset
		focus.b -= inset
		user32.NewProc("DrawFocusRect").Call(item.dc, uintptr(unsafe.Pointer(&focus)))
	}
	return true
}

func (w *wizard) box(x, y, width, height int) rect {
	return rect{int32(w.px(x)), int32(w.px(y)), int32(w.px(x + width)), int32(w.px(y + height))}
}

func (w *wizard) repaint() { user32.NewProc("InvalidateRect").Call(w.window, 0, 0) }

func (w *wizard) paint() {
	u, p := &w.ui, w.ui.colors
	var ps paintStruct
	dc, _, _ := user32.NewProc("BeginPaint").Call(w.window, uintptr(unsafe.Pointer(&ps)))
	defer user32.NewProc("EndPaint").Call(w.window, uintptr(unsafe.Pointer(&ps)))
	fill(dc, w.box(0, 0, clientWidth, clientHeight), u.brush(p.canvas))
	fill(dc, w.box(0, 0, 219, clientHeight), u.brush(p.sidebar))
	drawBitmap(dc, u.logo, w.box(25, 33, 64, 64))
	current := 0
	if edition == "community" {
		switch u.page {
		case pageReview, pageWork, pageError:
			current = 1
		case pageConnect:
			current = 2
		case pageDone:
			current = 3
		}
	} else if u.page == pageDone {
		current = 1
	}
	for i, h := range u.steps {
		y := 205 + i*46
		bg, dot := p.sidebar, p.sidebarText
		if i == current {
			bg, dot = p.selected, p.sea
			w.round(dc, w.box(15, y, 188, 40), 8, bg)
		} else if i < current {
			dot = p.sea
		}
		style := controlColor{bg, p.sidebarText}
		if i == current {
			style.fg = p.panel
		}
		if u.labels[h] != style {
			u.labels[h] = style
			user32.NewProc("InvalidateRect").Call(h, 0, 0)
		}
		w.round(dc, w.box(26, y+14, 9, 9), 5, dot)
	}
	card := func(x, y, width, height int) {
		w.round(dc, w.box(x, y, width, height), 13, p.edge)
		w.round(dc, w.box(x+1, y+1, width-2, height-2), 12, p.panel)
	}
	switch u.page {
	case pageBrand:
		card(249, 124, 600, 99)
		card(249, 230, 600, 154)
		w.round(dc, w.box(269, 248, 100, 100), 10, p.canvas)
		drawBitmap(dc, u.preview, w.box(269, 248, 100, 100))
	case pageReview:
		card(249, 121, 600, 198)
		card(249, 350, 600, 117)
	case pageWork:
		card(249, 136, 600, 135)
		w.round(dc, w.box(270, 221, 559, 12), 6, p.edge)
		if u.percent > 0 {
			w.round(dc, w.box(270, 221, 559*min(u.percent, 100)/100, 12), 6, p.deep)
		}
	case pageConnect:
		card(249, 128, 600, 345)
	case pageDone, pageError:
		card(249, 132, 600, 200)
	}
	fill(dc, w.box(249, 497, 600, 1), u.brush(p.edge))
}

func previewBitmap(img image.Image, bg color) bitmap {
	if img == nil {
		return bitmap{}
	}
	b := img.Bounds()
	ratio := max(float64(b.Dx())/128, float64(b.Dy())/128)
	width, height := max(1, int(float64(b.Dx())/max(ratio, 1))), max(1, int(float64(b.Dy())/max(ratio, 1)))
	result := bitmap{width, height, make([]byte, width*height*4)}
	for y := 0; y < height; y++ {
		for x := 0; x < width; x++ {
			r, g, blue, a := img.At(b.Min.X+x*b.Dx()/width, b.Min.Y+y*b.Dy()/height).RGBA()
			at := (y*width + x) * 4
			result.pixels[at], result.pixels[at+1], result.pixels[at+2] = byte(blue>>8+(uint32(bg>>16&255)*(65535-a)>>16)), byte(g>>8+(uint32(bg>>8&255)*(65535-a)>>16)), byte(r>>8+(uint32(bg&255)*(65535-a)>>16))
		}
	}
	return result
}

func drawBitmap(dc uintptr, img bitmap, to rect) {
	if len(img.pixels) == 0 {
		return
	}
	w, h := int(to.r-to.l), int(to.b-to.t)
	if w*img.height > h*img.width {
		scaled := h * img.width / img.height
		to.l += int32((w - scaled) / 2)
		w = scaled
	} else {
		scaled := w * img.height / img.width
		to.t += int32((h - scaled) / 2)
		h = scaled
	}
	head := bitmapHeader{size: 40, width: int32(img.width), height: -int32(img.height), planes: 1, bits: 32}
	gdi32.NewProc("SetStretchBltMode").Call(dc, 4)
	gdi32.NewProc("StretchDIBits").Call(dc, uintptr(to.l), uintptr(to.t), uintptr(w), uintptr(h), 0, 0, uintptr(img.width), uintptr(img.height), uintptr(unsafe.Pointer(&img.pixels[0])), uintptr(unsafe.Pointer(&head)), 0, 0x00CC0020)
}

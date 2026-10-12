// hold2tg clipboard history: a clipboard history like Windows' Win+V.
//
// GNOME Shell sees every copy (on Wayland nothing outside the compositor may
// watch the clipboard), so this extension records them: texts, and pictures
// up to 4 MB. Its shortcut (hold2tg's history_keys, Caps Lock+H by default)
// opens a panel in the middle of the screen; picking an
// item puts it on the clipboard and pastes it into the window you were in.
// The history is kept in ~/.local/state/hold2tg/ (only you can read it). Copies
// a password manager marks as secret are never recorded.
//
// hold2tg's daemon passes its settings on (the shortcut, the language, the size,
// and whether to record at all) and leaves the history to this extension while
// it runs; without it hold2tg falls back to a recorder and a window of its own.
//
// It also keeps hold2tg's Caps Lock hotkeys (catch-keys, e.g. <Mod3>c) from the
// apps: hold2tg times how long they are held by reading the keyboard itself,
// but Caps Lock is no modifier to the apps, so they would type "cccc…".

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {ensureActorVisibleInScrollView} from 'resource:///org/gnome/shell/misc/animationUtils.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'io.github.m_mardani.hold2tg.Clipboard';
const OBJECT_PATH = '/io/github/m_mardani/hold2tg/Clipboard';
const SECRET = 'x-kde-passwordManagerHint'; // KeePassXC and others mark secrets with it
const FILES = 'x-special/gnome-copied-files';
const TEXT_TYPES = ['text/plain;charset=utf-8', 'UTF8_STRING', 'text/plain', 'STRING', 'TEXT'];
const MAX_TEXT = 200000;             // characters; a longer copy is not kept
const MAX_IMAGE = 4 * 1024 * 1024;   // bytes, as on Windows
const MAX_PINNED = 100;
const CLIPBOARD = St.ClipboardType.CLIPBOARD;
// Physical keys (Linux input codes), so pasting works in any keyboard layout.
const KEY_LEFTCTRL = 29, KEY_LEFTSHIFT = 42, KEY_V = 47;
// Terminals paste with Ctrl+Shift+V.
const TERMINALS = ['terminal', 'ptyxis', 'console', 'kgx', 'kitty', 'alacritty', 'foot',
    'wezterm', 'tilix', 'terminator', 'konsole', 'xterm', 'urxvt', 'ghostty', 'blackbox'];

const STRINGS = {
    en: {
        title: 'Clipboard', search: 'Search', clear: 'Clear all', empty: 'Nothing copied yet',
        emptyHint: 'Everything you copy shows up here.', noMatch: 'No match', pin: 'Pin',
        remove: 'Delete', image: 'Picture', now: 'just now', minutes: '%d min ago',
        hours: '%d h ago', days: '%d days ago', lines: '%d lines', pinned: 'Pinned',
        help: 'Enter pastes · Esc closes',
    },
    fa: {
        title: 'کلیپ‌بورد', search: 'جست‌وجو', clear: 'پاک کردن همه', empty: 'هنوز چیزی کپی نشده',
        emptyHint: 'هر چیزی کپی کنید این‌جا می‌آید.', noMatch: 'چیزی پیدا نشد', pin: 'سنجاق',
        remove: 'حذف', image: 'تصویر', now: 'همین الان', minutes: '%d دقیقه پیش',
        hours: '%d ساعت پیش', days: '%d روز پیش', lines: '%d خط', pinned: 'سنجاق‌شده',
        help: '\u200FEnter برای جای‌گذاری · Esc برای بستن',
    },
};

const IFACE = `<node><interface name="${BUS_NAME}">
  <method name="Toggle"/>
  <method name="Show"/>
  <method name="Hide"/>
  <method name="Clear"/>
</interface></node>`;
// Only for the automated tests (HOLD2TG_CLIPBOARD_DEBUG=1 in the shell's environment).
const DEBUG_IFACE = `<node><interface name="${BUS_NAME}">
  <method name="Toggle"/>
  <method name="Show"/>
  <method name="Hide"/>
  <method name="Clear"/>
  <method name="IsOpen"><arg type="b" direction="out"/></method>
  <method name="Layout"><arg type="s" direction="out"/></method>
  <method name="Click"><arg type="d" direction="in"/><arg type="d" direction="in"/></method>
  <method name="CapsLock"><arg type="b" direction="out"/></method>
  <method name="UseInputSource"><arg type="u" direction="in"/></method>
  <method name="GetHistory"><arg type="s" direction="out"/></method>
  <method name="Screenshot"><arg type="s" direction="in"/></method>
  <method name="PressKeys"><arg type="au" direction="in"/></method>
  <method name="Choose"><arg type="i" direction="in"/></method>
</interface></node>`;

const FA_DIGITS = '۰۱۲۳۴۵۶۷۸۹';
const ARABIC_LETTER = /[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]/g;
const LETTER = /\p{L}/gu;

function fingerprint(text) {
    return GLib.compute_checksum_for_string(GLib.ChecksumType.SHA256, text, -1);
}

/** Mostly in Arabic script (Persian): shown right to left. */
function rightToLeft(text) {
    const letters = (text.match(LETTER) ?? []).length;
    return letters > 0 && (text.match(ARABIC_LETTER) ?? []).length * 2 > letters;
}

/** Sets the direction of an actor and everything in it. */
function setDirection(actor, direction) {
    actor.set_text_direction(direction);
    for (const child of actor.get_children())
        setDirection(child, direction);
}

/** For search: Arabic forms of letters match the Persian ones, case is ignored. */
function normalize(text) {
    return text.toLowerCase().replace(/ي/g, 'ی').replace(/ك/g, 'ک');
}

/** What a text item shows: its first lines, each cut short. */
function preview(text) {
    const lines = text.replace(/\t/g, '  ').split('\n').map(l => l.trim()).filter(l => l);
    const shown = lines.slice(0, 3).map(l => (l.length > 90 ? `${l.slice(0, 90)}…` : l));
    if (lines.length > 3)
        shown[2] += ' …';
    return shown.join('\n');
}

// --- the stored history -----------------------------------------------------------

class History {
    constructor(dir, size) {
        this._dir = dir;
        this._imageDir = GLib.build_filenamev([dir, 'shell-clipboard-images']);
        this._file = Gio.File.new_for_path(GLib.build_filenamev([dir, 'shell-clipboard.json']));
        this.size = size;
        this.items = [];   // newest first: {type: 'text', text} or {type: 'image', hash, mime}, with at, pinned
        this._saveId = 0;
        if (!this._load(this._file)) {
            // First run: take over what hold2tg's own recorder kept (texts only).
            if (this._load(Gio.File.new_for_path(GLib.build_filenamev([dir, 'clipboard.json'])))) {
                this._trim();
                this.save();
            }
        }
    }

    /** Reads a history file; false if there is none. */
    _load(file) {
        try {
            const [, bytes] = file.load_contents(null);
            const raw = JSON.parse(new TextDecoder().decode(bytes));
            for (const item of Array.isArray(raw?.items) ? raw.items : []) {
                const base = {at: Number(item.at) || 0, pinned: item.pinned === true};
                if (item.type === 'image' && /^[0-9a-f]{64}$/.test(item.hash ?? ''))
                    this.items.push({...base, type: 'image', hash: item.hash, mime: String(item.mime || 'image/png')});
                else if (typeof item.text === 'string')
                    this.items.push({...base, type: 'text', text: item.text});
            }
        } catch (e) {
            if (e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND))
                return false;
            console.warn(`hold2tg: the clipboard history was unreadable, starting afresh: ${e.message}`);
        }
        return true;
    }

    imagePath(item) {
        return GLib.build_filenamev([this._imageDir, `${item.hash}.img`]);
    }

    _put(item) {
        const old = this.items.find(i => (item.type === 'text' ? i.text === item.text : i.hash === item.hash));
        if (old)
            item.pinned = old.pinned;
        this.items = [item, ...this.items.filter(i => i !== old)];
        this._trim();
        this.save();
    }

    addText(text) {
        if (!text.trim() || text.length > MAX_TEXT)
            return false;
        this._put({type: 'text', text, at: Date.now() / 1000, pinned: false});
        return true;
    }

    addImage(bytes, mime) {
        const size = bytes.get_size();
        if (size === 0 || size > MAX_IMAGE)
            return false;
        const hash = GLib.compute_checksum_for_bytes(GLib.ChecksumType.SHA256, bytes);
        const item = {type: 'image', hash, mime, at: Date.now() / 1000, pinned: false};
        GLib.mkdir_with_parents(this._imageDir, 0o700);
        const file = Gio.File.new_for_path(this.imagePath(item));
        if (!file.query_exists(null)) {
            file.replace_contents_bytes_async(bytes, null, false,
                Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, null,
                (f, res) => {
                    try {
                        f.replace_contents_finish(res);
                    } catch (e) {
                        console.warn(`hold2tg: cannot save a copied picture: ${e.message}`);
                    }
                });
        }
        this._put(item);
        return true;
    }

    remove(item) {
        this.items = this.items.filter(i => i !== item);
        this._forget([item]);
        this.save();
    }

    togglePin(item) {
        item.pinned = !item.pinned;
        this._trim();
        this.save();
    }

    /** Like Windows: everything but the pinned items. */
    clear() {
        const gone = this.items.filter(i => !i.pinned);
        this.items = this.items.filter(i => i.pinned);
        this._forget(gone);
        this.save();
    }

    setSize(size) {
        this.size = size;
        this._trim();
        this.save();
    }

    _trim() {
        const pinned = this.items.filter(i => i.pinned).slice(0, MAX_PINNED);
        const rest = this.items.filter(i => !i.pinned).slice(0, this.size);
        const kept = new Set([...pinned, ...rest]);
        const gone = this.items.filter(i => !kept.has(i));
        this.items = this.items.filter(i => kept.has(i));
        this._forget(gone);
    }

    /** Deletes the picture files nothing refers to any more. */
    _forget(items) {
        for (const item of items) {
            if (item.type !== 'image' || this.items.some(i => i.hash === item.hash))
                continue;
            try {
                Gio.File.new_for_path(this.imagePath(item)).delete(null);
            } catch {
                // already gone
            }
        }
    }

    _bytes() {
        const items = this.items.map(i => (i.type === 'text'
            ? {type: 'text', text: i.text, at: i.at, pinned: i.pinned}
            : {type: 'image', hash: i.hash, mime: i.mime, at: i.at, pinned: i.pinned}));
        return new GLib.Bytes(new TextEncoder().encode(JSON.stringify({version: 2, items})));
    }

    /** Saved a moment later, so a burst of copies is written once. */
    save() {
        if (this._saveId)
            return;
        this._saveId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 400, () => {
            this._saveId = 0;
            GLib.mkdir_with_parents(this._dir, 0o700);
            this._file.replace_contents_bytes_async(this._bytes(), null, false,
                Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, null,
                (f, res) => {
                    try {
                        f.replace_contents_finish(res);
                    } catch (e) {
                        console.warn(`hold2tg: cannot save the clipboard history: ${e.message}`);
                    }
                });
            return GLib.SOURCE_REMOVE;
        });
    }

    /** Writes now, if a save is waiting (when the extension is turned off). */
    flush() {
        if (!this._saveId)
            return;
        GLib.source_remove(this._saveId);
        this._saveId = 0;
        try {
            GLib.mkdir_with_parents(this._dir, 0o700);
            this._file.replace_contents(this._bytes().toArray(), null, false,
                Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        } catch (e) {
            console.warn(`hold2tg: cannot save the clipboard history: ${e.message}`);
        }
    }
}

// --- the panel ---------------------------------------------------------------------

class Panel {
    constructor(ext) {
        this._ext = ext;
        this._root = null;
    }

    get isOpen() {
        return this._root !== null;
    }

    toggle() {
        if (this.isOpen)
            this.close();
        else
            this.open();
    }

    open() {
        if (this.isOpen)
            return;
        const s = this._ext.strings;
        const monitor = Main.layoutManager.currentMonitor ?? Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;

        // An invisible layer over the whole screen: a click outside the panel closes it.
        // Clicks inside go on untouched: stopping them would cancel the buttons' click
        // gestures, and no item, pin or delete button could be clicked.
        this._root = new St.Widget({reactive: true, x: 0, y: 0,
            width: global.stage.width, height: global.stage.height});
        this._root.connect('button-press-event', (_actor, event) => {
            if (this._panel?.contains(global.stage.get_event_actor(event)))
                return Clutter.EVENT_PROPAGATE;
            this.close();
            return Clutter.EVENT_STOP;
        });
        const holder = new St.Bin({x: monitor.x, y: monitor.y, width: monitor.width,
            height: monitor.height});
        this._root.add_child(holder);

        // Centred at the size the stylesheet gives it (filling the screen otherwise).
        const panel = new St.BoxLayout({style_class: 'h2c-panel',
            orientation: Clutter.Orientation.VERTICAL, reactive: true,
            x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
        holder.set_child(panel);

        const header = new St.BoxLayout({style_class: 'h2c-header'});
        header.add_child(new St.Label({text: s.title, style_class: 'h2c-title', x_expand: true,
            y_align: Clutter.ActorAlign.CENTER}));
        this._clearButton = new St.Button({label: s.clear, style_class: 'h2c-clear', can_focus: false});
        this._clearButton.connect('clicked', () => {
            this._ext.history.clear();
            this._fill();
        });
        header.add_child(this._clearButton);
        panel.add_child(header);

        this._entry = new St.Entry({style_class: 'h2c-search', hint_text: s.search, can_focus: true,
            x_expand: true});
        this._entry.clutter_text.connect('text-changed', () => this._filter());
        this._entry.clutter_text.connect('key-press-event', (_actor, event) => this._key(event));
        panel.add_child(this._entry);

        this._list = new St.BoxLayout({style_class: 'h2c-list', orientation: Clutter.Orientation.VERTICAL,
            x_expand: true});
        this._scroll = new St.ScrollView({style_class: 'h2c-scroll', x_expand: true, y_expand: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC,
            overlay_scrollbars: true, child: this._list});
        panel.add_child(this._scroll);
        this._notice = new St.Label({style_class: 'h2c-notice', x_align: Clutter.ActorAlign.CENTER,
            y_expand: true, y_align: Clutter.ActorAlign.CENTER});
        this._notice.clutter_text.line_wrap = true;
        panel.add_child(this._notice);
        panel.add_child(new St.Label({text: s.help, style_class: 'h2c-help',
            x_align: Clutter.ActorAlign.CENTER}));

        Main.layoutManager.modalDialogGroup.add_child(this._root);
        this._grab = Main.pushModal(this._root, {actionMode: Shell.ActionMode.SYSTEM_MODAL});
        this._panel = panel;
        this._fill();
        global.stage.set_key_focus(this._entry);
    }

    close() {
        if (!this._root)
            return;
        if (this._grab)
            Main.popModal(this._grab);
        this._grab = null;
        this._root.destroy();
        this._root = this._panel = this._entry = this._list = this._scroll = null;
        this._rows = [];
    }

    _ago(at) {
        const s = this._ext.strings;
        const seconds = Math.max(0, Date.now() / 1000 - at);
        let text = s.now;
        if (seconds >= 86400)
            text = s.days.replace('%d', Math.floor(seconds / 86400));
        else if (seconds >= 3600)
            text = s.hours.replace('%d', Math.floor(seconds / 3600));
        else if (seconds >= 60)
            text = s.minutes.replace('%d', Math.floor(seconds / 60));
        return this._digits(text);
    }

    _digits(text) {
        return this._ext.lang === 'fa' ? text.replace(/[0-9]/g, d => FA_DIGITS[d]) : text;
    }

    /** Builds the rows: the pinned items first, then the rest, newest first. */
    _fill() {
        const s = this._ext.strings;
        const history = this._ext.history;
        this._list.destroy_all_children();
        const items = [...history.items.filter(i => i.pinned), ...history.items.filter(i => !i.pinned)];
        this._rows = items.map(item => this._row(item));
        for (const row of this._rows)
            this._list.add_child(row);
        // The panel reads right to left in Persian; each text keeps its own direction.
        setDirection(this._panel, this._ext.lang === 'fa' ? Clutter.TextDirection.RTL : Clutter.TextDirection.LTR);
        for (const row of this._rows) {
            if (!row._label)
                continue;
            const rtl = rightToLeft(row._item.text);
            row._label.set_text_direction(rtl ? Clutter.TextDirection.RTL : Clutter.TextDirection.LTR);
            row._label.clutter_text.line_alignment = rtl ? Pango.Alignment.RIGHT : Pango.Alignment.LEFT;
        }
        this._clearButton.visible = history.items.some(i => !i.pinned);
        this._emptyText = items.length ? s.noMatch : `${s.empty}\n${s.emptyHint}`;
        this._filter();
    }

    _row(item) {
        const s = this._ext.strings;
        const row = new St.Button({style_class: 'h2c-item', x_expand: true, can_focus: false,
            reactive: true, track_hover: true});
        row._item = item;
        const box = new St.BoxLayout({x_expand: true, style_class: 'h2c-item-box'});
        const body = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true,
            style_class: 'h2c-item-body'});
        if (item.type === 'text') {
            const label = new St.Label({text: preview(item.text), style_class: 'h2c-text',
                x_expand: true, x_align: Clutter.ActorAlign.FILL});
            label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            body.add_child(label);
            row._label = label;
        } else {
            const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
            const picture = St.TextureCache.get_default().load_file_async(
                Gio.File.new_for_path(this._ext.history.imagePath(item)), 330, 110, scale,
                global.stage.get_resource_scale?.() ?? 1);
            body.add_child(new St.Bin({child: picture, x_align: Clutter.ActorAlign.START,
                style_class: 'h2c-image'}));
        }
        let meta = this._ago(item.at);
        if (item.type === 'text') {
            const lines = item.text.trim().split('\n').length;
            if (lines > 1)
                meta += ` · ${this._digits(s.lines.replace('%d', lines))}`;
        } else {
            meta = `${s.image} · ${meta}`;
        }
        body.add_child(new St.Label({text: meta, style_class: 'h2c-meta', x_align: Clutter.ActorAlign.START}));
        box.add_child(body);

        const actions = new St.BoxLayout({style_class: 'h2c-actions', y_align: Clutter.ActorAlign.START});
        const pin = new St.Button({style_class: `h2c-action${item.pinned ? ' h2c-pinned' : ''}`,
            child: new St.Icon({icon_name: 'view-pin-symbolic', icon_size: 16}), can_focus: false,
            accessible_name: s.pin});
        pin.connect('clicked', () => {
            this._ext.history.togglePin(item);
            this._fill();
        });
        const remove = new St.Button({style_class: 'h2c-action',
            child: new St.Icon({icon_name: 'edit-delete-symbolic', icon_size: 16}), can_focus: false,
            accessible_name: s.remove});
        remove.connect('clicked', () => {
            this._ext.history.remove(item);
            this._fill();
        });
        actions.add_child(pin);
        actions.add_child(remove);
        row._pin = pin;
        row._remove = remove;
        box.add_child(actions);
        row.set_child(box);
        row.connect('clicked', () => this._choose(item));
        return row;
    }

    _filter() {
        const query = normalize(this._entry.get_text().trim());
        this._visible = this._rows.filter(row => {
            const shown = !query || (row._item.type === 'text' && normalize(row._item.text).includes(query));
            row.visible = shown;
            return shown;
        });
        this._notice.text = this._emptyText;
        this._notice.visible = this._visible.length === 0;
        this._scroll.visible = this._visible.length > 0;
        this._select(0);
    }

    _select(index) {
        this._selected?.remove_style_pseudo_class('selected');
        this._selected = this._visible[Math.max(0, Math.min(index, this._visible.length - 1))] ?? null;
        if (this._selected) {
            this._selected.add_style_pseudo_class('selected');
            ensureActorVisibleInScrollView(this._scroll, this._selected);
        }
    }

    _move(step) {
        if (this._visible.length)
            this._select(this._visible.indexOf(this._selected) + step);
    }

    _key(event) {
        const sym = event.get_key_symbol();
        const state = event.get_state();
        const action = global.display.get_keybinding_action(event.get_key_code(), state);
        if (sym === Clutter.KEY_Escape || (action && action === this._ext.toggleAction)) {
            this.close();    // Esc, or the shortcut again
            return Clutter.EVENT_STOP;
        }
        const steps = {[Clutter.KEY_Down]: 1, [Clutter.KEY_KP_Down]: 1, [Clutter.KEY_Up]: -1,
            [Clutter.KEY_KP_Up]: -1, [Clutter.KEY_Page_Down]: 5, [Clutter.KEY_Page_Up]: -5};
        if (sym in steps) {
            this._move(steps[sym]);
            return Clutter.EVENT_STOP;
        }
        if (sym === Clutter.KEY_Return || sym === Clutter.KEY_KP_Enter || sym === Clutter.KEY_ISO_Enter) {
            if (this._selected)
                this._choose(this._selected._item);
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }

    /** For the tests: where the rows and their buttons are on the screen. */
    layout() {
        const box = actor => {
            const [x, y] = actor.get_transformed_position();
            const [w, h] = actor.get_transformed_size();
            return [x, y, w, h];
        };
        return (this._visible ?? []).map(row => ({
            text: row._item.type === 'text' ? row._item.text : null,
            row: box(row), pin: box(row._pin), remove: box(row._remove),
            selected: row === this._selected,
        }));
    }

    /** For the tests: picks the index-th item shown. */
    chooseShown(index) {
        const row = this._visible?.[index];
        if (row)
            this._choose(row._item);
    }

    _choose(item) {
        this.close();
        this._ext.use(item);
    }
}

// --- the extension -------------------------------------------------------------------

export default class ClipboardHistoryExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._setLanguage();
        this.history = new History(GLib.build_filenamev([GLib.get_user_state_dir(), 'hold2tg']),
            this._settings.get_int('history-size'));
        this._secrets = [];    // fingerprints of secrets seen, in memory only
        this._changes = 0;
        this._pasteId = 0;
        this._keyboard = global.stage.context.get_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._panel = new Panel(this);

        this._selection = global.display.get_selection();
        this._selection.connectObject('owner-changed', (_sel, type, source) => {
            if (type === Meta.SelectionType.SELECTION_CLIPBOARD)
                this._copied(source);
        }, this);
        this._copied(true);   // what is on the clipboard already

        this.toggleAction = Main.wm.addKeybinding('toggle-history', this._settings,
            Meta.KeyBindingFlags.IGNORE_AUTOREPEAT, Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
            () => this._panel.toggle());
        this._caught = [];
        this._catchKeys();
        this._settings.connectObject(
            'changed::history-size', () => this.history.setSize(this._settings.get_int('history-size')),
            'changed::language', () => this._setLanguage(),
            'changed::catch-keys', () => this._catchKeys(), this);

        const debug = GLib.getenv('HOLD2TG_CLIPBOARD_DEBUG') === '1';
        this._service = new Service(this, debug);
        this._dbus = Gio.DBusExportedObject.wrapJSObject(debug ? DEBUG_IFACE : IFACE, this._service);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.DBus.session.own_name(BUS_NAME, Gio.BusNameOwnerFlags.REPLACE, null, null);
    }

    disable() {
        Gio.DBus.session.unown_name(this._nameId);
        this._dbus.unexport();
        this._dbus = this._service = null;
        Main.wm.removeKeybinding('toggle-history');
        this._releaseKeys();
        this._settings.disconnectObject(this);
        this._selection.disconnectObject(this);
        this._selection = null;
        this._panel.close();
        this._panel = null;
        if (this._pasteId)
            GLib.source_remove(this._pasteId);
        this._pasteId = 0;
        this._keyboard.run_dispose();
        this._keyboard = null;
        this._pointer?.run_dispose();
        this._pointer = null;
        this.history.flush();
        this.history = null;
        this._secrets = null;
        this._settings = null;
    }

    /** Takes hold2tg's hotkeys from the apps: GNOME keeps them, and nothing happens. */
    _catchKeys() {
        this._releaseKeys();
        for (const accel of this._settings.get_strv('catch-keys')) {
            // Never a key on its own: that would take it from every app.
            if (!/^<[^>]+>/.test(accel)) {
                console.warn(`hold2tg: not taking ${accel}: a hotkey needs a modifier`);
                continue;
            }
            const action = global.display.grab_accelerator(accel, Meta.KeyBindingFlags.IGNORE_AUTOREPEAT);
            if (action === Meta.KeyBindingAction.NONE) {
                console.warn(`hold2tg: GNOME would not give out ${accel} (taken already?)`);
                continue;
            }
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.ALL);
            this._caught.push(action);
        }
    }

    _releaseKeys() {
        for (const action of this._caught) {
            global.display.ungrab_accelerator(action);
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.NONE);
        }
        this._caught = [];
    }

    _setLanguage() {
        let lang = this._settings.get_string('language');
        if (lang === 'auto')
            lang = hold2tgLanguage();
        this.lang = lang === 'fa' ? 'fa' : 'en';
        this.strings = STRINGS[this.lang];
    }

    /** The clipboard changed: record what is on it now. */
    _copied(owner) {
        const number = ++this._changes;
        if (!owner || !this._settings.get_boolean('record'))
            return;     // cleared, or the history is turned off
        const types = this._selection.get_mimetypes(Meta.SelectionType.SELECTION_CLIPBOARD);
        if (!types.length || types.includes(FILES))
            return;
        const clipboard = St.Clipboard.get_default();
        if (types.includes(SECRET)) {
            // Not recorded; and remembered, since GNOME keeps a copy without the mark
            // once the password manager quits, and that copy looks ordinary.
            clipboard.get_text(CLIPBOARD, (_c, text) => {
                if (text && this._secrets) {
                    this._secrets.push(fingerprint(text));
                    this._secrets.splice(0, this._secrets.length - 50);
                }
            });
            return;
        }
        if (types.some(t => TEXT_TYPES.includes(t))) {
            clipboard.get_text(CLIPBOARD, (_c, text) => {
                if (number !== this._changes || !text || !this.history)
                    return;     // something newer came meanwhile, or turned off
                if (this._secrets.includes(fingerprint(text)))
                    return;
                this.history.addText(text);
            });
            return;
        }
        const image = types.find(t => t === 'image/png') ?? types.find(t => t.startsWith('image/'));
        if (image) {
            clipboard.get_content(CLIPBOARD, image, (_c, bytes) => {
                if (number === this._changes && bytes && this.history)
                    this.history.addImage(bytes, image);
            });
        }
    }

    /** An item was picked: onto the clipboard, and pasted into the window. */
    use(item) {
        const clipboard = St.Clipboard.get_default();
        if (item.type === 'text') {
            clipboard.set_text(CLIPBOARD, item.text);
        } else {
            try {
                const [, contents] = GLib.file_get_contents(this.history.imagePath(item));
                clipboard.set_content(CLIPBOARD, item.mime, new GLib.Bytes(contents));
            } catch (e) {
                console.warn(`hold2tg: the copied picture is gone: ${e.message}`);
                this.history.remove(item);
                return;
            }
        }
        if (!this._settings.get_boolean('paste-on-select'))
            return;
        if (this._pasteId)
            GLib.source_remove(this._pasteId);
        // Once the panel has let go of the keyboard and the window has it back.
        this._pasteId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 80, () => {
            this._pasteId = 0;
            this._paste();
            return GLib.SOURCE_REMOVE;
        });
    }

    _paste() {
        const window = global.display.get_focus_window();
        if (!window)
            return;
        const app = `${window.get_wm_class() ?? ''} ${window.get_sandboxed_app_id?.() ?? ''}`.toLowerCase();
        const keys = TERMINALS.some(t => app.includes(t))
            ? [KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V] : [KEY_LEFTCTRL, KEY_V];
        this.pressKeys(keys);
    }

    /** For the tests: a real pointer click. */
    click(x, y) {
        if (!this._pointer) {
            this._pointer = global.stage.context.get_backend().get_default_seat()
                .create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        }
        // Paced like a hand: moved there, then pressed, then let go.
        const steps = [
            () => this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y),
            () => this._pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY,
                Clutter.ButtonState.PRESSED),
            () => this._pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY,
                Clutter.ButtonState.RELEASED),
        ];
        steps.forEach((step, i) => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1 + i * 60, () => {
            step();
            return GLib.SOURCE_REMOVE;
        }));
    }

    pressKeys(keys) {
        const time = GLib.get_monotonic_time();
        for (const key of keys)
            this._keyboard.notify_key(time, key, Clutter.KeyState.PRESSED);
        for (const key of [...keys].reverse())
            this._keyboard.notify_key(time, key, Clutter.KeyState.RELEASED);
    }

    get panel() {
        return this._panel;
    }
}

/** hold2tg's own language setting ([behavior] language), else the desktop's. */
function hold2tgLanguage() {
    try {
        const file = GLib.build_filenamev([GLib.get_user_config_dir(), 'hold2tg', 'config.ini']);
        const [, bytes] = GLib.file_get_contents(file);
        let section = '';
        for (const raw of new TextDecoder().decode(bytes).split('\n')) {
            const line = raw.trim();
            const header = /^\[(.+)\]$/.exec(line);
            if (header)
                section = header[1].trim();
            const pair = /^language\s*=\s*(\w+)/.exec(line);
            if (section === 'behavior' && pair)
                return pair[1].toLowerCase();
        }
    } catch {
        // no hold2tg config
    }
    return GLib.get_language_names().some(l => l.startsWith('fa')) ? 'fa' : 'en';
}

class Service {
    constructor(ext, debug) {
        this._ext = ext;
        this._debug = debug;
    }

    Toggle() {
        this._ext.panel.toggle();
    }

    Show() {
        this._ext.panel.open();
    }

    Hide() {
        this._ext.panel.close();
    }

    Clear() {
        this._ext.history.clear();
        if (this._ext.panel.isOpen) {
            this._ext.panel.close();
            this._ext.panel.open();
        }
    }

    Layout() {
        return JSON.stringify(this._ext.panel.layout());
    }

    Click(x, y) {
        this._ext.click(x, y);
    }

    CapsLock() {
        return global.stage.context.get_backend().get_default_seat().get_keymap().get_caps_lock_state();
    }

    async UseInputSourceAsync([index], invocation) {
        const keyboard = await import('resource:///org/gnome/shell/ui/status/keyboard.js');
        keyboard.getInputSourceManager().inputSources[index]?.activate(true);
        invocation.return_value(null);
    }

    IsOpen() {
        return this._ext.panel.isOpen;
    }

    GetHistory() {
        return JSON.stringify(this._ext.history.items);
    }

    PressKeys(keys) {
        this._ext.pressKeys(keys);
    }

    Choose(index) {
        this._ext.panel.chooseShown(index);
    }

    async ScreenshotAsync([path], invocation) {
        try {
            Gio._promisify(Shell.Screenshot.prototype, 'screenshot');   // a no-op if done already
            const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.NONE, null);
            await new Shell.Screenshot().screenshot(false, stream);
            stream.close(null);
            invocation.return_value(null);
        } catch (e) {
            invocation.return_error_literal(Gio.IOErrorEnum, Gio.IOErrorEnum.FAILED, e.message);
        }
    }
}

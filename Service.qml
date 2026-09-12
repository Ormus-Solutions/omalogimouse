import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  property var shell: null
  property var manifest: null

  property bool connected: false
  property string name: "Omalogimouse"
  property string family: "Omalogimouse"
  property string generation: ""
  property string serial: ""
  property string selectedSerial: ""
  property bool pinDevice: false
  property var devices: []
  property bool hasDpi: false
  property bool hasHires: false
  property bool hasRatchet: false
  property bool hasSmartShift: false
  property bool hasHaptic: false
  property bool hasButtons: false
  property bool acceleration: true
  property var buttons: []
  property var actionCatalog: []
  property string wpid: ""
  property int battery: -1
  property string batteryStatus: ""
  property int dpi: 1000
  property int dpiMin: 200
  property int dpiMax: 8000
  property int dpiStep: 50
  property bool hires: false
  property bool ratcheted: true
  property int smartShift: 10
  property int smartShiftMin: 1
  property int smartShiftMax: 50
  property int haptic: 60
  property int hapticMin: 0
  property int hapticMax: 100
  property string host: ""
  property var hosts: []
  property string lastError: ""
  property bool refreshing: false
  property bool busy: statusProc.running || actionProc.running
  property bool initialized: false

  readonly property string helperPath: {
    var url = Qt.resolvedUrl("mx.py")
    var s = String(url)
    if (s.indexOf("file://") === 0) s = decodeURIComponent(s.substring(7))
    if (s.indexOf("localhost/") === 0) s = s.substring(9)
    return s
  }

  function settingMap(obj, name) {
    if (!obj || !obj.settings) return null
    return obj.settings[name] || null
  }

  function asInt(value, fallback) {
    var n = parseInt(String(value), 10)
    return isFinite(n) ? n : fallback
  }

  function asBool(value) {
    if (value === true || value === 1) return true
    var s = String(value || "").toLowerCase()
    return s === "true" || s === "on" || s === "yes" || s === "1"
  }

  function capText(raw, maxChars) {
    var text = String(raw || "")
    if (text.length > maxChars) return text.substring(0, maxChars)
    return text
  }

  function applyStatus(raw) {
    var text = capText(raw, 65536).trim()
    if (text === "") {
      lastError = "Empty helper output"
      return
    }
    var parsed
    try {
      parsed = JSON.parse(text)
    } catch (e) {
      lastError = "Could not parse mouse status"
      return
    }
    if (!parsed || parsed.ok === false) {
      connected = false
      lastError = String(parsed && parsed.error ? parsed.error : "Mouse helper failed")
      initialized = true
      return
    }

    connected = parsed.connected === true
    name = String(parsed.name || parsed.family || "Omalogimouse")
    family = String(parsed.family || name)
    generation = String(parsed.generation || "")
    serial = String(parsed.serial || "")
    if (!pinDevice && serial !== "") selectedSerial = serial
    devices = parsed.devices instanceof Array ? parsed.devices : []
    hasDpi = parsed.hasDpi === true
    hasHires = parsed.hasHires === true
    hasRatchet = parsed.hasRatchet === true
    hasSmartShift = parsed.hasSmartShift === true
    hasHaptic = parsed.hasHaptic === true
    buttons = parsed.buttons instanceof Array ? parsed.buttons : []
    actionCatalog = parsed.actionCatalog instanceof Array ? parsed.actionCatalog : []
    hasButtons = parsed.hasButtons === true || buttons.length > 0
    acceleration = parsed.acceleration !== false
    wpid = String(parsed.wpid || "")
    battery = parsed.battery === null || parsed.battery === undefined ? -1 : asInt(parsed.battery, -1)
    batteryStatus = String(parsed.batteryStatus || "")
    lastError = connected ? "" : String(parsed.error || "No mouse found")

    var dpiS = settingMap(parsed, "dpi")
    if (dpiS) {
      dpi = asInt(dpiS.raw !== undefined ? dpiS.raw : dpiS.value, dpi)
      dpiMin = asInt(dpiS.min, 200)
      dpiMax = asInt(dpiS.max, 8000)
      dpiStep = asInt(dpiS.step, 50)
    }
    var hiresS = settingMap(parsed, "hires-smooth-resolution")
    if (hiresS) hires = asBool(hiresS.raw !== undefined ? hiresS.raw : hiresS.value)
    var ratchetS = settingMap(parsed, "scroll-ratchet")
    if (ratchetS) {
      var rv = String(ratchetS.value || "")
      ratcheted = rv.toLowerCase().indexOf("free") < 0
    }
    var shiftS = settingMap(parsed, "smart-shift")
    if (shiftS) {
      smartShift = asInt(shiftS.raw !== undefined ? shiftS.raw : shiftS.value, smartShift)
      smartShiftMin = asInt(shiftS.min, 1)
      smartShiftMax = asInt(shiftS.max, 50)
    }
    var hapS = settingMap(parsed, "haptic-level")
    if (hapS) {
      haptic = asInt(hapS.raw !== undefined ? hapS.raw : hapS.value, haptic)
      hapticMin = asInt(hapS.min, 0)
      hapticMax = asInt(hapS.max, 100)
    } else {
      hasHaptic = false
    }
    var hostS = settingMap(parsed, "change-host")
    if (hostS) {
      host = String(hostS.value || "")
      hosts = hostS.choices instanceof Array ? hostS.choices : []
    }
    initialized = true
    if (connected && hasButtons && !listenProc.running) startListen()
  }

  property string statusLine: ""
  property string statusErrLine: ""
  property string actionLine: ""
  property string actionErrLine: ""
  property int listenBytes: 0

  function runHelper(args) {
    var cmd = ["/usr/bin/timeout", "-s", "KILL", "8", "/usr/bin/python3.14", "-I", helperPath]
    if (pinDevice && selectedSerial !== "") {
      cmd.push("--device")
      cmd.push(selectedSerial)
    }
    for (var i = 0; i < args.length; i++) cmd.push(String(args[i]))
    return cmd
  }

  function selectDevice(serial) {
    selectedSerial = String(serial || "")
    pinDevice = selectedSerial !== ""
    refresh()
  }

  function refresh() {
    if (statusProc.running) return
    refreshing = true
    statusProc.command = runHelper(["status"])
    statusProc.running = true
  }

  function setSetting(name, value) {
    if (actionProc.running) return
    lastError = ""
    actionProc.command = runHelper(["set", name, String(value)])
    actionProc.running = true
  }

  function setDpi(value) {
    var step = dpiStep > 0 ? dpiStep : 50
    var snapped = Math.round(asInt(value, dpi) / step) * step
    if (snapped < dpiMin) snapped = dpiMin
    if (snapped > dpiMax) snapped = dpiMax
    dpi = snapped
    setSetting("dpi", snapped)
  }

  function setHires(on) {
    hires = on === true
    setSetting("hires-smooth-resolution", hires ? "true" : "false")
  }

  function setAcceleration(on) {
    if (actionProc.running) return
    acceleration = on === true
    lastError = ""
    actionProc.command = runHelper(["accel", acceleration ? "on" : "off"])
    actionProc.running = true
  }

  function setRatcheted(on) {
    ratcheted = on === true
    setSetting("scroll-ratchet", ratcheted ? "Ratcheted" : "Freespinning")
  }

  function setSmartShift(value) {
    smartShift = asInt(value, smartShift)
    setSetting("smart-shift", smartShift)
  }

  function setHaptic(value) {
    haptic = asInt(value, haptic)
    setSetting("haptic-level", haptic)
  }

  function playHaptic() {
    if (actionProc.running || !hasHaptic) return
    actionProc.command = runHelper(["play", "SHARP STATE CHANGE"])
    actionProc.running = true
  }

  function setButton(name, action) {
    if (actionProc.running) return
    lastError = ""
    actionProc.command = runHelper(["bind", String(name), String(action)])
    actionProc.running = true
  }

  function startListen() {
    if (listenProc.running) listenProc.running = false
    if (!connected || !hasButtons) return
    listenBytes = 0
    var cmd = ["/usr/bin/timeout", "-s", "KILL", "21600", "/usr/bin/python3.14", "-I", helperPath]
    if (pinDevice && selectedSerial !== "") {
      cmd.push("--device")
      cmd.push(selectedSerial)
    }
    cmd.push("listen")
    listenProc.command = cmd
    listenProc.running = true
  }

  Timer {
    interval: 30000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Process {
    id: statusProc
    running: false
    command: []
    stdout: SplitParser {
      onRead: function(line) {
        if (line.length > 65536) { statusProc.running = false; return }
        root.statusLine = line
      }
    }
    stderr: SplitParser {
      onRead: function(line) {
        if (line.length > 256) return
        root.statusErrLine = line
      }
    }
    onExited: function(exitCode) {
      root.refreshing = false
      if (exitCode === 0) root.applyStatus(root.statusLine)
      else root.lastError = root.statusErrLine || root.statusLine || "Could not read mouse"
    }
  }

  Process {
    id: actionProc
    running: false
    command: []
    stdout: SplitParser {
      onRead: function(line) {
        if (line.length > 65536) { actionProc.running = false; return }
        root.actionLine = line
      }
    }
    stderr: SplitParser {
      onRead: function(line) {
        if (line.length > 256) return
        root.actionErrLine = line
      }
    }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.applyStatus(root.actionLine)
        var cmd = actionProc.command || []
        for (var i = 0; i < cmd.length; i++) {
          if (cmd[i] === "bind") { root.startListen(); break }
        }
      } else {
        root.lastError = root.actionErrLine || root.actionLine || "Mouse command failed"
      }
    }
  }

  Process {
    id: listenProc
    running: false
    command: []
    stdout: SplitParser {
      onRead: function(line) {
        root.listenBytes += line.length
        if (root.listenBytes > 262144) listenProc.running = false
      }
    }
  }
}

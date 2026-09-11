pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "omalogimouse"

  property var anchorItem: null
  property var hostWidget: null
  property var mouse: null
  readonly property var barIdentity: hostWidget || root
  readonly property var svc: mouse || (bar && bar.shell ? bar.shell.serviceFor(moduleName) : null)
  readonly property var panelScreen: anchorItem && anchorItem.QsWindow.window
    ? anchorItem.QsWindow.window.screen : null
  readonly property var mainScreen: {
    var best = null
    var screens = Quickshell.screens
    for (var i = 0; i < screens.length; i++) {
      if (!best || screens[i].width * screens[i].height > best.width * best.height)
        best = screens[i]
    }
    return best
  }
  ipcTarget: panelScreen && panelScreen === mainScreen ? moduleName : ""

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool ready: !!svc && svc.initialized === true
  readonly property bool connected: ready && svc.connected === true
  property int tabIndex: 0
  readonly property bool onBinds: tabIndex === 1

  function open() { root.controller.show() }
  function close() { root.controller.hide() }
  function toggle() { opened ? close() : open() }
  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function")
      return bar.switchPanelFrom(barIdentity, direction)
    return false
  }

  onOpenedChanged: if (opened && svc) svc.refresh()

  function hostLabel() {
    var h = ready ? String(svc.host || "") : ""
    var colon = h.indexOf(":")
    return colon >= 0 ? h.substring(colon + 1) : h
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    padding: Style.space(14)
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(760))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if ((t === "r" || t === "R") && root.svc) root.svc.refresh()
        else if ((t === "h" || t === "H") && root.connected && root.svc.hasHaptic) root.svc.playHaptic()
        else if (t === "1") root.tabIndex = 0
        else if (t === "2") root.tabIndex = 1
      }

      Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: false
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        flickableDirection: Flickable.VerticalFlick

        Column {
          id: column
          width: flick.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: root.ready ? (root.svc.family || root.svc.name) : "Omalogimouse"
            meta: root.connected
              ? ((root.svc.battery >= 0 ? root.svc.battery + "% · " : "") + (root.hostLabel() !== "" ? root.hostLabel() : "connected"))
              : "Offline"
            detail: root.connected ? (root.svc.dpi + " DPI") : ""
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.connected ? 1 : 0.45
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: "󰍽"
                color: root.foreground
                font.pixelSize: Style.font.display
              }
            }
          }

          Text {
            visible: root.svc && root.svc.lastError !== ""
            width: parent.width
            textFormat: Text.PlainText
            text: root.svc ? root.svc.lastError : ""
            color: bar && bar.urgent ? bar.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.ready && root.svc.devices && root.svc.devices.length > 1

            Repeater {
              model: root.ready && root.svc.devices ? root.svc.devices.length : 0
              Button {
                required property int index
                readonly property var device: root.svc.devices[index]
                width: parent.width
                text: ((device && (device.family || device.name)) || "Omalogimouse")
                  + (device && device.online ? "" : " · offline")
                selected: !!(root.svc && device && device.serial === root.svc.serial)
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: if (root.svc && device) root.svc.selectDevice(device.serial)
              }
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(6)

            Button {
              width: (parent.width - parent.spacing) / 2
              text: "Mouse"
              selected: !root.onBinds
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.tabIndex = 0
            }

            Button {
              width: (parent.width - parent.spacing) / 2
              text: "Keybinds"
              selected: root.onBinds
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.tabIndex = 1
            }
          }

          StackLayout {
            id: pages
            width: parent.width
            currentIndex: root.tabIndex
            height: currentIndex === 0 ? mousePage.implicitHeight : bindsPage.implicitHeight

            Column {
              id: mousePage
              width: pages.width
              spacing: Style.space(12)

              Toggle {
                width: parent.width
                label: "Mouse acceleration"
                description: "Off is a flat pointer on this mouse only, not the touchpad"
                checked: !!(root.svc && root.svc.acceleration)
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: if (root.svc) root.svc.setAcceleration(!root.svc.acceleration)
              }

              Toggle {
                width: parent.width
                visible: root.connected && root.svc.hasHires
                label: "Hi-res scroll"
                description: "Smoother MagSpeed scrolling"
                checked: root.connected && root.svc.hires
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: if (root.svc) root.svc.setHires(!root.svc.hires)
              }

              Toggle {
                width: parent.width
                visible: root.connected && root.svc.hasRatchet
                label: "Ratchet"
                description: "Off is free-spin"
                checked: root.connected && root.svc.ratcheted
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: if (root.svc) root.svc.setRatcheted(!root.svc.ratcheted)
              }

              Column {
                width: parent.width
                spacing: Style.space(4)
                visible: root.connected && root.svc.hasDpi

                Item {
                  width: parent.width
                  height: dpiTitle.implicitHeight
                  Text {
                    id: dpiTitle
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: "DPI"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.subtitle
                    font.bold: true
                  }
                  Text {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: String(dpiSlider.dragging ? Math.round(dpiSlider.liveValue) : (root.svc ? root.svc.dpi : 0))
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }

                PanelSlider {
                  id: dpiSlider
                  width: parent.width
                  bar: root.bar
                  minimum: root.svc ? root.svc.dpiMin : 200
                  maximum: root.svc ? root.svc.dpiMax : 8000
                  step: root.svc ? root.svc.dpiStep : 50
                  integer: true
                  value: root.svc ? root.svc.dpi : 1000
                  onReleased: function(v) { if (root.svc) root.svc.setDpi(v) }
                }
              }

              Column {
                width: parent.width
                spacing: Style.space(4)
                visible: root.connected && root.svc.hasSmartShift

                Item {
                  width: parent.width
                  height: shiftTitle.implicitHeight
                  Text {
                    id: shiftTitle
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: "SmartShift"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.subtitle
                    font.bold: true
                  }
                  Text {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: String(shiftSlider.dragging ? Math.round(shiftSlider.liveValue) : (root.svc ? root.svc.smartShift : 0))
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }

                PanelSlider {
                  id: shiftSlider
                  width: parent.width
                  bar: root.bar
                  minimum: root.svc ? root.svc.smartShiftMin : 1
                  maximum: root.svc ? root.svc.smartShiftMax : 50
                  step: 1
                  integer: true
                  value: root.svc ? root.svc.smartShift : 10
                  onReleased: function(v) { if (root.svc) root.svc.setSmartShift(v) }
                }
              }

              Column {
                width: parent.width
                spacing: Style.space(4)
                visible: root.connected && root.svc.hasHaptic

                Item {
                  width: parent.width
                  height: hapticTitle.implicitHeight
                  Text {
                    id: hapticTitle
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: "Haptic"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.subtitle
                    font.bold: true
                  }
                  Text {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: String(hapticSlider.dragging ? Math.round(hapticSlider.liveValue) : (root.svc ? root.svc.haptic : 0))
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }

                PanelSlider {
                  id: hapticSlider
                  width: parent.width
                  bar: root.bar
                  minimum: root.svc ? root.svc.hapticMin : 0
                  maximum: root.svc ? root.svc.hapticMax : 100
                  step: 1
                  integer: true
                  value: root.svc ? root.svc.haptic : 60
                  onReleased: function(v) { if (root.svc) root.svc.setHaptic(v) }
                }
              }

              Button {
                visible: root.connected && root.svc.hasHaptic
                width: parent.width
                text: "Tap haptic"
                foreground: root.foreground
                fontFamily: root.fontFamily
                bordered: true
                onClicked: if (root.svc) root.svc.playHaptic()
              }
            }

            Column {
              id: bindsPage
              width: pages.width
              spacing: Style.space(8)

              Text {
                visible: !root.connected || !root.svc || !root.svc.buttons || root.svc.buttons.length === 0
                width: parent.width
                textFormat: Text.PlainText
                text: root.connected ? "No remappable buttons on this mouse" : "Connect the mouse to edit button actions"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.WordWrap
              }

              Repeater {
                model: root.svc && root.svc.buttons ? root.svc.buttons.length : 0
                Dropdown {
                  required property int index
                  readonly property var button: root.svc.buttons[index]
                  width: parent.width
                  label: button ? button.name : ""
                  value: button ? button.action : ""
                  options: root.svc && root.svc.actionCatalog ? root.svc.actionCatalog : []
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onChanged: function(v) {
                    if (root.svc && button && v !== button.action)
                      root.svc.setButton(button.name, v)
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}

import QtQuick
import qs.Commons
import qs.Ui

// Bar widget: envelope icon that opens the mail panel.
// Follows the weather/tailscale pattern — a thin loader that forwards
// open/close/toggle to Panel.qml and injects bar/anchor/hostWidget.

BarWidget {
  id: root
  moduleName: "omarmail"

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function refresh() {
    if (panelLoader.item && panelLoader.item.refresh) panelLoader.item.refresh()
  }

  function togglePanel() {
    if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle()
  }

  function openMessage(id) {
    if (panelLoader.item && panelLoader.item.openMessage) panelLoader.item.openMessage(id)
  }

  // Shape contract for shell.summon/hide/toggle routing.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

  function open() {
    if (panelLoader.item && panelLoader.item.openFromHotkey) panelLoader.item.openFromHotkey()
  }

  function close() {
    if (panelLoader.item && panelLoader.item.close) panelLoader.item.close()
  }

  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  visible: true
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    slotSize: Style.bar.iconSlot
    tooltipText: (panelLoader.item && panelLoader.item.unreadCount > 0)
      ? "Omarmail (" + panelLoader.item.unreadCount + " unread)"
      : "Omarmail"

    iconComponent: Component {
      Item {
        anchors.fill: parent

        Text {
          anchors.centerIn: parent
          text: "\uf0e0"
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.bar.iconFont
          color: (panelLoader.item && panelLoader.item.opened)
            ? (root.bar && root.bar.activeColor ? root.bar.activeColor : Color.accent)
            : (root.bar ? root.bar.foreground : Color.foreground)
        }

        Rectangle {
          visible: panelLoader.item && panelLoader.item.unreadCount > 0
          width: Style.space(5)
          height: Style.space(5)
          radius: width / 2
          color: Color.accent
          anchors.top: parent.top
          anchors.topMargin: Style.space(1)
          anchors.right: parent.right
          anchors.rightMargin: Style.space(1)
        }
      }
    }

    onPressed: function(b) {
      if (!root.bar) return
      if (b === Qt.RightButton) root.refresh()
      else if (b === Qt.MiddleButton) root.refresh()
      else root.togglePanel()
    }
  }
}


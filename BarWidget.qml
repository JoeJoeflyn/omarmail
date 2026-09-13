import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar widget: envelope icon that opens the mail panel.
// Follows the weather/tailscale pattern — a thin loader that forwards
// open/close/toggle to Panel.qml and injects bar/anchor/hostWidget.

BarWidget {
  id: root
  moduleName: "omarmail"

  property string pendingPanelAction: ""
  property string pendingMessageId: ""

  function ensurePanel() {
    if (!panelLoader.active) panelLoader.active = true
  }

  function runPendingPanelAction() {
    var panel = panelLoader.item
    if (!panel || pendingPanelAction === "") return
    root.injectPanel()
    var action = pendingPanelAction
    var messageId = pendingMessageId
    pendingPanelAction = ""
    pendingMessageId = ""
    if (action === "toggle" && panel.toggle) panel.toggle()
    else if (action === "open" && panel.openFromHotkey) panel.openFromHotkey()
    else if (action === "message" && panel.openMessage) panel.openMessage(messageId)
    else if (action === "refresh" && panel.refresh) panel.refresh()
  }

  function queuePanelAction(action, messageId) {
    pendingPanelAction = action
    pendingMessageId = messageId || ""
    ensurePanel()
    runPendingPanelAction()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function refresh() { queuePanelAction("refresh", "") }

  function togglePanel() { queuePanelAction("toggle", "") }

  function openMessage(id) { queuePanelAction("message", id) }

  // Shape contract for shell.summon/hide/toggle routing.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

  function open() { queuePanelAction("open", "") }

  function close() {
    pendingPanelAction = ""
    pendingMessageId = ""
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

  IpcHandler {
    target: "omarmail"
    function open() { root.open() }
    function close() { root.close() }
    function show() { root.open() }
    function hide() { root.close() }
    function toggle() { root.togglePanel() }
    function refresh() { root.refresh() }
    function openMessage(id: string) { root.openMessage(id) }
  }

  Loader {
    id: panelLoader
    active: false
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(function() {
        root.injectPanel()
        root.runPendingPanelAction()
      })
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


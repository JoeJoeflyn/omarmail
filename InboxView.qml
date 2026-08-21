import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Inbox list view — header, search, states, email rows, pagination
Flickable {
  id: root
  property var p  // Panel root
  property alias searchField: searchField

  visible: p.viewMode === "inbox"
  anchors.fill: parent
  contentWidth: width
  contentHeight: col.implicitHeight
  clip: true
  boundsBehavior: Flickable.StopAtBounds
  interactive: contentHeight > height
  ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

  Column {
    id: col
    width: root.width
    spacing: Style.space(10)

    // Header Row
    Item {
      width: parent.width
      implicitHeight: Math.max(headerLeft.implicitHeight, headerRight.implicitHeight) + Style.space(4)

      Row {
        id: headerLeft
        anchors.left: parent.left; anchors.leftMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(8)

        Text { text: "\uf0e0"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.title; anchors.verticalCenter: parent.verticalCenter }
        Text { text: p.gmailSearch ? "Search Results" : (p.searchMode ? "Filtered Inbox" : "Inbox"); color: p.foreground; font.family: p.fontFamily; font.pixelSize: Style.font.title; font.bold: true; anchors.verticalCenter: parent.verticalCenter }

        BorderSurface {
          visible: p.unreadCount > 0 && !p.searchMode
          anchors.verticalCenter: parent.verticalCenter
          implicitWidth: badge.implicitWidth + Style.space(12)
          implicitHeight: badge.implicitHeight + Style.space(4)
          color: Style.selectedFillFor(p.foreground, Color.accent)
          borderSpec: Border.controlSpec("normal", p.foreground, Color.accent)
          radius: Style.cornerRadius

          Text {
            id: badge
            anchors.centerIn: parent; textFormat: Text.PlainText
            text: p.unreadCount + " unread"; color: Color.accent
            font.family: p.fontFamily; font.pixelSize: Style.font.caption; font.bold: true
          }
        }
      }

      Row {
        id: headerRight
        anchors.right: parent.right; anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(4)

        PanelActionButton {
          iconText: "\uf002"; tooltipText: p.searchOpen ? "Hide search" : "Search emails"
          foreground: p.foreground; hoverColor: Color.accent; fontFamily: p.fontFamily
          onClicked: {
            p.searchOpen = !p.searchOpen
            if (p.searchOpen) searchField.forceActiveFocus()
            else if (p.searchMode) p.clearSearch()
          }
        }

        PanelActionButton {
          iconText: "\uf021"; tooltipText: "Refresh inbox"
          foreground: p.foreground; hoverColor: Color.accent; fontFamily: p.fontFamily
          onClicked: p.refresh()
          RotationAnimator on rotation { running: p.listProcRunning || p.searchProcRunning; from: 0; to: 360; duration: 800; loops: Animation.Infinite }
        }
      }
    }

    // Search Field
    Item {
      visible: p.searchOpen || p.searchMode
      width: parent.width
      implicitHeight: searchField.implicitHeight + Style.space(2)

      TextField {
        id: searchField
        anchors.left: parent.left; anchors.leftMargin: Style.space(10)
        anchors.right: parent.right; anchors.rightMargin: Style.space(10)
        placeholderText: "Search subject, sender, or from:..."
        foreground: p.foreground; accent: Color.accent; font.pixelSize: Style.font.bodySmall

        onTextChanged: {
          var q = text.trim()
          if (q === "") { if (p.searchMode) p.clearSearch() }
          else if (Model.isGmailOperator(q)) searchDebounce.restart()
          else { searchDebounce.stop(); p.runSearch(q, "") }
        }
        Keys.onEscapePressed: { text = ""; p.clearSearch(); p.searchOpen = false }
      }

      Timer { id: searchDebounce; interval: 600; onTriggered: { var q = searchField.text.trim(); if (q !== "" && Model.isGmailOperator(q)) p.runSearch(q, "") } }
    }

    PanelSeparator { foreground: p.foreground }

    // Auth needed
    Column {
      visible: p.needsAuth && !p.authInProgress
      width: parent.width - Style.space(32); anchors.horizontalCenter: parent.horizontalCenter
      spacing: Style.space(12); topPadding: Style.space(20); bottomPadding: Style.space(20)

      Text { text: "\uf0e0"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.display; anchors.horizontalCenter: parent.horizontalCenter }
      Text { text: "Connect Google Account"; color: p.foreground; font.family: p.fontFamily; font.pixelSize: Style.font.title; font.bold: true; anchors.horizontalCenter: parent.horizontalCenter }
      Text { text: "Sign in with Google OAuth in your browser.\nNo passwords stored."; color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.bodySmall; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; anchors.horizontalCenter: parent.horizontalCenter }
      Button { anchors.horizontalCenter: parent.horizontalCenter; text: "Connect Gmail"; iconText: "\uf090"; bordered: true; foreground: Color.accent; onClicked: p.startAuth() }
      Text { visible: p.errorMsg !== ""; text: p.errorMsg; color: p.urgent; font.family: p.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; width: parent.width }
    }

    // Auth in progress
    Column {
      visible: p.authInProgress
      width: parent.width - Style.space(32); anchors.horizontalCenter: parent.horizontalCenter
      spacing: Style.space(12); topPadding: Style.space(24); bottomPadding: Style.space(24)

      Text {
        text: "\uf110"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.display
        anchors.horizontalCenter: parent.horizontalCenter
        RotationAnimator on rotation { running: p.authInProgress; from: 0; to: 360; duration: 1000; loops: Animation.Infinite }
      }
      Text { text: "Waiting for browser login..."; color: p.foreground; font.family: p.fontFamily; font.pixelSize: Style.font.body; font.bold: true; anchors.horizontalCenter: parent.horizontalCenter }
      Text { text: "Complete the sign-in in your browser window."; color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.bodySmall; horizontalAlignment: Text.AlignHCenter; anchors.horizontalCenter: parent.horizontalCenter }
    }

    // General error
    Item {
      visible: p.ready && p.errorMsg !== "" && !p.needsAuth && !p.authInProgress
      width: parent.width; implicitHeight: errText.implicitHeight + Style.space(24)
      Text { id: errText; anchors.centerIn: parent; width: parent.width - Style.space(32); textFormat: Text.PlainText; text: p.errorMsg; color: p.urgent; font.family: p.fontFamily; font.pixelSize: Style.font.bodySmall; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter }
    }

    // Loading
    Column {
      visible: !p.ready && !p.needsAuth && !p.authInProgress
      width: parent.width; spacing: Style.space(12); topPadding: Style.space(32); bottomPadding: Style.space(32)
      Text { text: "\uf110"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.title; anchors.horizontalCenter: parent.horizontalCenter; RotationAnimator on rotation { running: !p.ready; from: 0; to: 360; duration: 900; loops: Animation.Infinite } }
      Text { textFormat: Text.PlainText; text: "Loading inbox..."; color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.bodySmall; anchors.horizontalCenter: parent.horizontalCenter }
    }

    // Empty
    Column {
      visible: p.ready && p.envelopes.length === 0 && p.errorMsg === "" && !p.needsAuth && !p.authInProgress
      width: parent.width; spacing: Style.space(8); topPadding: Style.space(24); bottomPadding: Style.space(24)
      Text { text: "\uf00c"; color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.title; anchors.horizontalCenter: parent.horizontalCenter }
      Text { textFormat: Text.PlainText; text: p.searchMode ? "No matching emails found" : "All caught up! Inbox is clear."; color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.bodySmall; anchors.horizontalCenter: parent.horizontalCenter }
    }

    // Email Rows
    Repeater {
      model: p.envelopes
      delegate: CursorSurface {
        id: emailRow
        required property var modelData
        required property int index
        readonly property var env: {
          var rev = p.envelopesRevision
          return p.getEnvelope(modelData.id) || modelData
        }
        readonly property bool isSeen: {
          var rev = p.envelopesRevision
          return p.isEnvelopeSeen(emailRow.env.id)
        }
        readonly property bool isHovered: rowHover.hovered

        hasCursor: p.cursorActive && p.cursorIndex === index
        foreground: p.foreground; accent: Color.accent
        width: col.width; implicitHeight: rowLayout.implicitHeight + Style.space(12)

        HoverHandler { id: rowHover; onHoveredChanged: { if (hovered) { p.cursorActive = true; p.cursorIndex = emailRow.index } } }

        MouseArea {
          id: rowMouse
          anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
          anchors.right: actionRow.visible ? actionRow.left : parent.right
          cursorShape: Qt.PointingHandCursor
          onClicked: p.openDetail(emailRow.env)
        }

        Row {
          id: rowLayout
          anchors.left: parent.left; anchors.leftMargin: Style.space(12)
          anchors.right: parent.right; anchors.rightMargin: Style.space(12)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(10)

          // Unread dot
          Item {
            width: Style.space(8); height: Style.space(8); anchors.verticalCenter: parent.verticalCenter
            Rectangle { visible: !emailRow.isSeen; anchors.centerIn: parent; width: Style.space(6); height: Style.space(6); radius: width / 2; color: Color.accent }
          }

          // Avatar
          BorderSurface {
            anchors.verticalCenter: parent.verticalCenter
            implicitWidth: Style.space(28); implicitHeight: Style.space(28); radius: Style.cornerRadius
            color: emailRow.isSeen ? Style.hoverFillFor(p.foreground, Color.accent) : Style.selectedFillFor(p.foreground, Color.accent)
            borderSpec: Border.controlSpec("normal", p.foreground, Color.accent)
            Text { anchors.centerIn: parent; textFormat: Text.PlainText; text: Model.senderInitials(emailRow.env); color: emailRow.isSeen ? p.dim : p.foreground; font.family: p.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
          }

          // Sender, Subject, Date
          Column {
            width: parent.width - Style.space(46) - (emailRow.isHovered ? actionRow.implicitWidth + Style.space(8) : Style.space(8))
            anchors.verticalCenter: parent.verticalCenter; spacing: Style.space(2)

            Row {
              width: parent.width; spacing: Style.space(6)
              Text { textFormat: Text.PlainText; text: Model.senderName(emailRow.env); color: emailRow.isSeen ? p.dim : p.foreground; font.family: p.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: !emailRow.isSeen; elide: Text.ElideRight; width: parent.width - dateText.implicitWidth - Style.space(6) }
              Text { id: dateText; textFormat: Text.PlainText; text: Model.formatDate(emailRow.env.date); color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.caption; horizontalAlignment: Text.AlignRight }
            }

            Text { width: parent.width; textFormat: Text.PlainText; text: Model.subject(emailRow.env); color: emailRow.isSeen ? p.dim : p.foreground; font.family: p.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: !emailRow.isSeen; elide: Text.ElideRight }
          }

          // Action buttons
          Row {
            id: actionRow; z: 10; visible: emailRow.isHovered; anchors.verticalCenter: parent.verticalCenter; spacing: Style.space(4)
            PanelActionButton { iconText: emailRow.isSeen ? "\uf0e0" : "\uf2b7"; tooltipText: emailRow.isSeen ? "Mark as unread" : "Mark as read"; foreground: p.foreground; hoverColor: Color.accent; fontFamily: p.fontFamily; onClicked: p.toggleSeen(emailRow.env.id, emailRow.isSeen) }
            PanelActionButton { iconText: "\uf014"; tooltipText: "Move to trash"; foreground: p.foreground; hoverColor: p.urgent; fontFamily: p.fontFamily; onClicked: p.deleteMessage(emailRow.env.id) }
          }
        }
      }
    }

    // Pagination
    Item {
      visible: !p.needsAuth && !p.authInProgress && p.envelopes.length > 0
      width: parent.width; implicitHeight: pagRow.implicitHeight + Style.space(8)

      Row {
        id: pagRow; anchors.centerIn: parent; spacing: Style.space(12)
        PanelActionButton { iconText: "\uf053"; tooltipText: "Previous page"; foreground: p.foreground; hoverColor: Color.accent; fontFamily: p.fontFamily; enabled: p.currentPage > 1 && !p.searchMode; opacity: enabled ? 1.0 : 0.35; onClicked: p.prevPage() }
        Text { anchors.verticalCenter: parent.verticalCenter; textFormat: Text.PlainText; text: p.searchMode ? (p.envelopes.length + " results") : ("Page " + p.currentPage); color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
        PanelActionButton { iconText: "\uf054"; tooltipText: "Next page"; foreground: p.foreground; hoverColor: Color.accent; fontFamily: p.fontFamily; enabled: p.searchMode ? p.hasMorePages : p.envelopes.length >= p.pageSize; opacity: enabled ? 1.0 : 0.35; onClicked: p.nextPage() }
      }
    }
  }
}

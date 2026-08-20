import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Email detail view — top nav, header card, scrollable body
Column {
  id: root
  property var p  // Panel root

  visible: p.viewMode === "detail"
  anchors.fill: parent
  spacing: Style.space(8)

  // Top Navigation Bar
  Item {
    id: topNav
    width: parent.width
    implicitHeight: Math.max(backBtn.implicitHeight, actions.implicitHeight) + Style.space(4)

    Button {
      id: backBtn
      anchors.left: parent.left; anchors.leftMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      text: "Inbox"; iconText: "\uf060"; fontFamily: p.fontFamily; fontSize: Style.font.bodySmall
      bordered: false; foreground: p.foreground; accent: Color.accent
      onClicked: p.backToInbox()
    }

    Row {
      id: actions
      anchors.right: parent.right; anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter; spacing: Style.space(4)

      PanelActionButton {
        iconText: (p.selectedEnvelope && Model.isSeen(p.selectedEnvelope)) ? "\uf0e0" : "\uf2b7"
        tooltipText: (p.selectedEnvelope && Model.isSeen(p.selectedEnvelope)) ? "Mark as unread" : "Mark as read"
        foreground: p.foreground; hoverColor: Color.accent; fontFamily: p.fontFamily
        onClicked: { if (p.selectedEnvelope) p.toggleSeen(p.selectedEnvelope.id, Model.isSeen(p.selectedEnvelope)) }
      }

      PanelActionButton {
        iconText: "\uf014"; tooltipText: "Delete email"
        foreground: p.foreground; hoverColor: p.urgent; fontFamily: p.fontFamily
        onClicked: { if (p.selectedEnvelope) p.deleteMessage(p.selectedEnvelope.id) }
      }

      PanelActionButton {
        iconText: "\uf08e"; tooltipText: "Open in Gmail Web"
        foreground: p.foreground; hoverColor: Color.accent; fontFamily: p.fontFamily
        onClicked: { if (p.selectedId) p.openInGmail(p.selectedId) }
      }
    }
  }

  PanelSeparator { id: sep1; foreground: p.foreground }

  // Email Header Card
  Column {
    id: headerCard
    width: parent.width - Style.space(24); anchors.horizontalCenter: parent.horizontalCenter
    spacing: Style.space(6)

    Text {
      width: parent.width; textFormat: Text.PlainText
      text: p.currentDetail ? p.currentDetail.subject : (p.selectedEnvelope ? Model.subject(p.selectedEnvelope) : "")
      color: p.foreground; font.family: p.fontFamily; font.pixelSize: Style.font.title; font.bold: true; wrapMode: Text.Wrap
    }

    Row {
      width: parent.width; spacing: Style.space(8)

      BorderSurface {
        anchors.verticalCenter: parent.verticalCenter
        implicitWidth: Style.space(32); implicitHeight: Style.space(32); radius: Style.cornerRadius
        color: Style.selectedFillFor(p.foreground, Color.accent)
        borderSpec: Border.controlSpec("normal", p.foreground, Color.accent)
        Text { anchors.centerIn: parent; textFormat: Text.PlainText; text: p.currentDetail && p.currentDetail.from_initials ? p.currentDetail.from_initials : (p.selectedEnvelope ? Model.senderInitials(p.selectedEnvelope) : "?"); color: p.foreground; font.family: p.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true }
      }

      Column {
        anchors.verticalCenter: parent.verticalCenter; width: parent.width - Style.space(40); spacing: Style.space(1)

        Row {
          width: parent.width; spacing: Style.space(6)
          Text { textFormat: Text.PlainText; text: p.currentDetail && p.currentDetail.from_name ? p.currentDetail.from_name : (p.selectedEnvelope ? Model.senderName(p.selectedEnvelope) : "Unknown"); color: p.foreground; font.family: p.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true; elide: Text.ElideRight }
          Text {
            textFormat: Text.PlainText
            text: {
              var em = p.currentDetail && p.currentDetail.from_email ? p.currentDetail.from_email : (p.selectedEnvelope ? Model.senderEmail(p.selectedEnvelope) : "")
              return em ? "<" + em + ">" : ""
            }
            color: p.dim
            font.family: p.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
          }
        }

        Row {
          width: parent.width; spacing: Style.space(8)
          Text { textFormat: Text.PlainText; text: p.currentDetail && p.currentDetail.date_formatted ? p.currentDetail.date_formatted : (p.selectedEnvelope ? Model.formatFullDate(p.selectedEnvelope.date) : ""); color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.caption }
          Row {
            visible: p.currentDetail && p.currentDetail.has_attachments
            spacing: Style.space(4); anchors.verticalCenter: parent.verticalCenter
            Text { text: "\uf0c6"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.caption }
            Text { textFormat: Text.PlainText; text: "Attachment"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.caption }
          }
        }
      }
    }
  }

  PanelSeparator { id: sep2; foreground: p.foreground }

  // Scrollable Email Body
  Item {
    id: bodyContainer
    width: parent.width
    height: Math.max(Style.space(220), p.keyCatcherHeight - topNav.implicitHeight - sep1.implicitHeight - headerCard.implicitHeight - sep2.implicitHeight - root.spacing * 5)
    clip: true

    // Loading
    Column {
      visible: p.loadingDetail; anchors.centerIn: parent; spacing: Style.space(8)
      Text { text: "\uf110"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.display; anchors.horizontalCenter: parent.horizontalCenter; RotationAnimator on rotation { running: p.loadingDetail; from: 0; to: 360; duration: 900; loops: Animation.Infinite } }
      Text { textFormat: Text.PlainText; text: "Loading email content..."; color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.caption; anchors.horizontalCenter: parent.horizontalCenter }
    }

    // Body
    Flickable {
      id: bodyScroll
      visible: !p.loadingDetail; anchors.fill: parent
      contentWidth: width; contentHeight: bodyCol.implicitHeight + Style.space(20)
      clip: true; boundsBehavior: Flickable.StopAtBounds; interactive: contentHeight > height
      ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

      Column {
        id: bodyCol
        width: bodyScroll.width - Style.space(24); anchors.horizontalCenter: parent.horizontalCenter
        spacing: Style.space(10); topPadding: Style.space(4); bottomPadding: Style.space(16)

        TextEdit {
          width: parent.width
          text: p.currentDetail ? (p.currentDetail.body_html || p.currentDetail.body || "(No message text)") : ""
          textFormat: TextEdit.RichText; readOnly: true; selectByMouse: true
          color: p.foreground; font.family: p.fontFamily; font.pixelSize: Style.font.bodySmall
          wrapMode: TextEdit.Wrap
          onLinkActivated: function(link) { Qt.openUrlExternally(link) }
        }
      }
    }
  }
}

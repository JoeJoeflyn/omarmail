import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Email detail view — High-polish Apple / Superhuman grade email reader
Item {
  id: root
  property var p  // Panel root

  visible: p.viewMode === "detail"
  anchors.fill: parent

  // Top Navigation & Action Header
  Item {
    id: topNav
    anchors.top: parent.top
    anchors.left: parent.left
    anchors.right: parent.right
    implicitHeight: Math.max(backBtn.implicitHeight, actions.implicitHeight) + Style.space(6)

    // Back to Inbox Button
    Button {
      id: backBtn
      anchors.left: parent.left; anchors.leftMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      text: "Inbox"
      iconText: "\uf060"
      fontFamily: p.fontFamily
      fontSize: Style.font.bodySmall
      bordered: true
      foreground: p.foreground
      accent: Color.accent
      onClicked: p.backToInbox()
    }

    // Header Quick Actions
    Row {
      id: actions
      anchors.right: parent.right; anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(6)

      PanelActionButton {
        iconText: (p.selectedEnvelope && Model.isSeen(p.selectedEnvelope)) ? "\uf0e0" : "\uf2b7"
        tooltipText: (p.selectedEnvelope && Model.isSeen(p.selectedEnvelope)) ? "Mark as unread (u)" : "Mark as read (u)"
        foreground: p.foreground
        hoverColor: Color.accent
        fontFamily: p.fontFamily
        onClicked: { if (p.selectedEnvelope) p.toggleSeen(p.selectedEnvelope.id, Model.isSeen(p.selectedEnvelope)) }
      }

      PanelActionButton {
        iconText: "\uf014"
        tooltipText: "Move to Trash (d)"
        foreground: p.foreground
        hoverColor: p.urgent
        fontFamily: p.fontFamily
        onClicked: { if (p.selectedEnvelope) p.deleteMessage(p.selectedEnvelope.id) }
      }

      PanelActionButton {
        iconText: "\uf08e"
        tooltipText: "Open in Gmail Web (o)"
        foreground: p.foreground
        hoverColor: Color.accent
        fontFamily: p.fontFamily
        onClicked: { if (p.selectedId) p.openInGmail(p.selectedId) }
      }
    }
  }

  PanelSeparator {
    id: sep1
    anchors.top: topNav.bottom
    anchors.topMargin: Style.space(2)
    anchors.left: parent.left
    anchors.right: parent.right
    foreground: p.foreground
  }

  // Elevated Email Header Card
  BorderSurface {
    id: headerCard
    anchors.top: sep1.bottom
    anchors.topMargin: Style.space(8)
    anchors.left: parent.left
    anchors.leftMargin: Style.space(10)
    anchors.right: parent.right
    anchors.rightMargin: Style.space(10)
    implicitHeight: headerContent.implicitHeight + Style.space(20)
    radius: Style.cornerRadius
    color: Qt.rgba(p.foreground.r, p.foreground.g, p.foreground.b, 0.035)
    borderSpec: Border.controlSpec("normal", p.foreground, Color.accent)

    Column {
      id: headerContent
      anchors.left: parent.left; anchors.leftMargin: Style.space(12)
      anchors.right: parent.right; anchors.rightMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(8)

      // Subject Title
      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: p.currentDetail ? p.currentDetail.subject : (p.selectedEnvelope ? Model.subject(p.selectedEnvelope) : "")
        color: p.foreground
        font.family: p.fontFamily
        font.pixelSize: Style.font.title
        font.bold: true
        wrapMode: Text.Wrap
      }

      // Sender Info Row
      Row {
        width: parent.width
        spacing: Style.space(10)

        // Sender Avatar Circle
        BorderSurface {
          anchors.verticalCenter: parent.verticalCenter
          implicitWidth: Style.space(36); implicitHeight: Style.space(36)
          radius: implicitWidth / 2
          color: Style.selectedFillFor(p.foreground, Color.accent)
          borderSpec: Border.controlSpec("normal", p.foreground, Color.accent)
          clip: true

          Image {
            id: senderAvatarImg
            anchors.fill: parent
            visible: status === Image.Ready && source !== ""
            source: (p.currentDetail && p.currentDetail.avatar_url) ? p.currentDetail.avatar_url : ""
            fillMode: Image.PreserveAspectCrop
            smooth: true
            mipmap: true
          }

          Text {
            anchors.centerIn: parent
            visible: !senderAvatarImg.visible
            textFormat: Text.PlainText
            text: p.currentDetail && p.currentDetail.from_initials ? p.currentDetail.from_initials : (p.selectedEnvelope ? Model.senderInitials(p.selectedEnvelope) : "?")
            color: Color.accent
            font.family: p.fontFamily
            font.pixelSize: Style.font.bodySmall
            font.bold: true
          }
        }

        // Sender Name, Email, Date, and Recipient Details
        Column {
          anchors.verticalCenter: parent.verticalCenter
          width: parent.width - Style.space(48)
          spacing: Style.space(2)

          Row {
            width: parent.width
            spacing: Style.space(6)

            Text {
              textFormat: Text.PlainText
              text: p.currentDetail && p.currentDetail.from_name ? p.currentDetail.from_name : (p.selectedEnvelope ? Model.senderName(p.selectedEnvelope) : "Unknown")
              color: p.foreground
              font.family: p.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
              elide: Text.ElideRight
            }

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
              anchors.verticalCenter: parent.verticalCenter
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(10)

            // Date Tag
            Text {
              textFormat: Text.PlainText
              text: p.currentDetail && p.currentDetail.date_formatted ? p.currentDetail.date_formatted : (p.selectedEnvelope ? Model.formatFullDate(p.selectedEnvelope.date) : "")
              color: p.dim
              font.family: p.fontFamily
              font.pixelSize: Style.font.caption
            }

            // Recipient Tag
            Text {
              visible: p.currentDetail && p.currentDetail.to && p.currentDetail.to.length > 0
              textFormat: Text.PlainText
              text: {
                if (!p.currentDetail || !p.currentDetail.to || p.currentDetail.to.length === 0) return ""
                var first = p.currentDetail.to[0]
                var toName = (first && typeof first === "object") ? (first.name || first.email || "") : String(first)
                return "to: " + (toName ? toName : "me")
              }
              color: p.dim
              font.family: p.fontFamily
              font.pixelSize: Style.font.caption
            }

            // Attachment indicator
            Row {
              visible: p.currentDetail && p.currentDetail.has_attachments
              spacing: Style.space(4)
              anchors.verticalCenter: parent.verticalCenter

              Text { text: "\uf0c6"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.caption }
              Text {
                textFormat: Text.PlainText
                text: (p.currentDetail && p.currentDetail.attachments) ? (p.currentDetail.attachments.length + " Attachment" + (p.currentDetail.attachments.length > 1 ? "s" : "")) : "Attachment"
                color: Color.accent
                font.family: p.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
              }
            }
          }
        }
      }

      // Attachment Chips Row (if attachments exist)
      Flow {
        visible: p.currentDetail && p.currentDetail.attachments && p.currentDetail.attachments.length > 0
        width: parent.width
        spacing: Style.space(6)

        Repeater {
          model: (p.currentDetail && p.currentDetail.attachments) ? p.currentDetail.attachments : []
          delegate: BorderSurface {
            id: attChip
            required property var modelData
            implicitWidth: attRow.implicitWidth + Style.space(16)
            implicitHeight: attRow.implicitHeight + Style.space(8)
            radius: Style.cornerRadius
            color: Style.selectedFillFor(p.foreground, Color.accent)
            borderSpec: Border.controlSpec("normal", p.foreground, Color.accent)

            Row {
              id: attRow
              anchors.centerIn: parent
              spacing: Style.space(6)
              Text { text: "\uf016"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.caption; anchors.verticalCenter: parent.verticalCenter }
              Text {
                textFormat: Text.PlainText
                text: attChip.modelData.filename || "file"
                color: p.foreground
                font.family: p.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                elide: Text.ElideMiddle
                width: Math.min(implicitWidth, Style.space(200))
                anchors.verticalCenter: parent.verticalCenter
              }
            }
          }
        }
      }
    }
  }

  // Elevated Reading Body Card
  BorderSurface {
    id: bodyContainer
    anchors.top: headerCard.bottom
    anchors.topMargin: Style.space(8)
    anchors.left: parent.left
    anchors.leftMargin: Style.space(10)
    anchors.right: parent.right
    anchors.rightMargin: Style.space(10)
    anchors.bottom: footerHints.top
    anchors.bottomMargin: Style.space(6)
    radius: Style.cornerRadius
    color: Qt.rgba(0, 0, 0, 0.25)
    borderSpec: Border.controlSpec("normal", p.foreground, Color.accent)
    clip: true

    // Loading State
    Column {
      visible: p.loadingDetail
      anchors.centerIn: parent
      spacing: Style.space(10)

      Text {
        text: "\uf110"
        color: Color.accent
        font.family: p.fontFamily
        font.pixelSize: Style.font.display
        anchors.horizontalCenter: parent.horizontalCenter
        RotationAnimator on rotation { running: p.loadingDetail; from: 0; to: 360; duration: 900; loops: Animation.Infinite }
      }
      Text {
        textFormat: Text.PlainText
        text: "Loading email content..."
        color: p.dim
        font.family: p.fontFamily
        font.pixelSize: Style.font.bodySmall
        anchors.horizontalCenter: parent.horizontalCenter
      }
    }

    // Scrollable Email Content
    Flickable {
      id: bodyScroll
      visible: !p.loadingDetail
      anchors.fill: parent
      contentWidth: width
      contentHeight: bodyCol.implicitHeight + Style.space(32)
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      interactive: contentHeight > height
      ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

      Column {
        id: bodyCol
        width: bodyScroll.width - Style.space(28)
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: Style.space(12)
        topPadding: Style.space(14)
        bottomPadding: Style.space(20)

        TextEdit {
          width: parent.width
          text: p.currentDetail ? (p.currentDetail.body_html || p.currentDetail.body || "(No message text)") : ""
          textFormat: TextEdit.RichText
          readOnly: true
          selectByMouse: true
          color: p.foreground
          font.family: p.fontFamily
          font.pixelSize: Style.font.body
          wrapMode: TextEdit.Wrap
          selectByKeyboard: true
          selectionColor: Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.4)
          selectedTextColor: p.foreground
          onLinkActivated: function(link) { Qt.openUrlExternally(link) }
        }
      }
    }
  }

  // Bottom Keyboard Shortcut Hints
  Item {
    id: footerHints
    anchors.bottom: parent.bottom
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottomMargin: Style.space(4)
    implicitHeight: hintsRow.implicitHeight + Style.space(4)

    Row {
      id: hintsRow
      anchors.centerIn: parent
      spacing: Style.space(16)

      Row {
        spacing: Style.space(4)
        Text { text: "Esc"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
        Text { text: "Back"; color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.caption }
      }

      Row {
        spacing: Style.space(4)
        Text { text: "u"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
        Text { text: "Toggle read"; color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.caption }
      }

      Row {
        spacing: Style.space(4)
        Text { text: "d"; color: p.urgent; font.family: p.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
        Text { text: "Trash"; color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.caption }
      }

      Row {
        spacing: Style.space(4)
        Text { text: "o"; color: Color.accent; font.family: p.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
        Text { text: "Open in web"; color: p.dim; font.family: p.fontFamily; font.pixelSize: Style.font.caption }
      }
    }
  }
}

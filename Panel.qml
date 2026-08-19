import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "omarmail"
  ipcTarget: "omarmail"
  manageIpc: false

  property var anchorItem: null
  property bool openedFromHotkey: false
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color surface: Color.popups.background
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // ---- State

  property string viewMode: "inbox"  // "inbox" | "detail"
  property var envelopes: []
  property var allEnvelopes: []
  property string errorMsg: ""
  property bool ready: false
  property bool authInProgress: false
  property bool needsAuth: false

  // Detail view state
  property string selectedId: ""
  property var selectedEnvelope: null
  property var currentDetail: null
  property bool loadingDetail: false

  // Search + pagination
  property string searchQuery: ""
  property bool searchMode: false
  property bool searchOpen: false
  property bool gmailSearch: false
  property int currentPage: 1
  property int pageSize: 10
  property string searchNextPage: ""
  property bool hasMorePages: false

  // Cursor navigation for keyboard
  property int cursorIndex: 0
  property bool cursorActive: false

  // Optimistic tracking
  property string pendingFlagId: ""
  property string pendingMoveId: ""

  readonly property int unreadCount: Model.unreadCount(allEnvelopes.length > 0 ? allEnvelopes : envelopes)
  property string label: "\uf0e0"

  // ---- Lifecycle

  function open() {
    openedFromHotkey = false
    viewMode = "inbox"
    setCenterHoverRevealSuppressed(false)
    root.controller.show()
    root.refresh()
  }

  function openFromHotkey() {
    openedFromHotkey = true
    viewMode = "inbox"
    root.controller.show()
    root.refresh()
    Qt.callLater(function() {
      if (root.opened) setCenterHoverRevealSuppressed(true)
    })
  }

  function close() {
    setCenterHoverRevealSuppressed(false)
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.openFromHotkey()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function setCenterHoverRevealSuppressed(value) {
    if (root.bar && "centerHoverRevealSuppressed" in root.bar)
      root.bar.centerHoverRevealSuppressed = value
  }

  // ---- Data Actions

  function refresh() {
    if (searchMode && !gmailSearch && searchQuery !== "") {
      currentPage = 1
      if (!listProc.running) listProc.running = true
    } else if (gmailSearch) {
      runSearch(searchQuery, "")
    } else {
      currentPage = 1
      if (!listProc.running) listProc.running = true
    }
    if (viewMode === "detail" && selectedId !== "") {
      readMessage(selectedId)
    }
  }

  function runSearch(query, pageToken) {
    searchQuery = query
    searchMode = true
    errorMsg = ""

    if (Model.isGmailOperator(query)) {
      gmailSearch = true
      searchProc.command = ["python3", Qt.resolvedUrl("search.py").toString().replace("file://", ""), query, String(pageSize), pageToken]
      searchProc.running = true
    } else {
      gmailSearch = false
      envelopes = Model.fuzzyFilter(allEnvelopes, query)
    }
  }

  function clearSearch() {
    searchMode = false
    gmailSearch = false
    searchQuery = ""
    searchNextPage = ""
    hasMorePages = false
    currentPage = 1
    envelopes = allEnvelopes
  }

  function nextPage() {
    if (gmailSearch) {
      if (searchNextPage !== "") runSearch(searchQuery, searchNextPage)
    } else {
      currentPage++
      listProc.command = ["himalaya", "envelope", "list", "--json", "-s", String(pageSize), "-p", String(currentPage)]
      listProc.running = true
    }
  }

  function prevPage() {
    if (gmailSearch) return
    if (currentPage > 1) {
      currentPage--
      listProc.command = ["himalaya", "envelope", "list", "--json", "-s", String(pageSize), "-p", String(currentPage)]
      listProc.running = true
    }
  }

  function openDetail(env) {
    if (!env || !env.id) return
    selectedId = env.id
    selectedEnvelope = env
    viewMode = "detail"
    readMessage(env.id)
    if (!Model.isSeen(env)) {
      markRead(env.id)
    }
  }

  function backToInbox() {
    viewMode = "inbox"
    selectedId = ""
    selectedEnvelope = null
    currentDetail = null
    loadingDetail = false
  }

  function readMessage(id) {
    loadingDetail = true
    readProc.running = false
    readProc.command = ["python3", Qt.resolvedUrl("read.py").toString().replace("file://", ""), id]
    readProc.running = true
  }

  function markRead(id) {
    pendingFlagId = id
    flagProc.running = false
    flagProc.command = ["himalaya", "flag", "add", "-f", "seen", id]
    flagProc.running = true
  }

  function markUnread(id) {
    pendingFlagId = id
    flagProc.running = false
    flagProc.command = ["himalaya", "flag", "remove", "-f", "seen", id]
    flagProc.running = true
  }

  function toggleSeen(id, currentSeen) {
    if (currentSeen) markUnread(id)
    else markRead(id)
  }

  function deleteMessage(id) {
    pendingMoveId = id
    moveProc.running = false
    moveProc.command = ["himalaya", "message", "move", "--to", "Trash", id]
    moveProc.running = true
    if (viewMode === "detail" && selectedId === id) {
      backToInbox()
    }
  }

  function openInGmail(id) {
    Qt.openUrlExternally("https://mail.google.com/mail/u/0/#inbox/" + id)
  }

  function startAuth() {
    authInProgress = true
    needsAuth = false
    errorMsg = ""
    authProc.running = true
  }

  // Optimistic local update — toggle seen flag
  function localToggleSeen(id) {
    var updated = []
    for (var i = 0; i < allEnvelopes.length; i++) {
      var env = allEnvelopes[i]
      if (env.id === id) {
        var flags = env.flags || []
        var hasSeen = Model.isSeen(env)
        if (hasSeen) {
          flags = flags.filter(function(f) {
            var name = typeof f === "string" ? f.toLowerCase() : (f && f.iana ? String(f.iana).toLowerCase() : "")
            return name !== "seen"
          })
        } else {
          flags.push({raw: "\\Seen", iana: "seen"})
        }
        env.flags = flags
        if (selectedEnvelope && selectedEnvelope.id === id) {
          selectedEnvelope.flags = flags
        }
      }
      updated.push(env)
    }
    allEnvelopes = updated
    envelopes = searchMode && !gmailSearch ? Model.fuzzyFilter(allEnvelopes, searchQuery) : allEnvelopes
  }

  // Optimistic local update — remove envelope
  function localRemove(id) {
    allEnvelopes = allEnvelopes.filter(function(env) { return env.id !== id })
    envelopes = searchMode && !gmailSearch ? Model.fuzzyFilter(allEnvelopes, searchQuery) : allEnvelopes
  }

  // ---- Processes

  Process {
    id: listProc
    command: ["himalaya", "envelope", "list", "--json", "-s", String(pageSize), "-p", "1"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var raw = String(text || "").trim()
        var result = Model.parseEnvelopeList(raw)
        root.allEnvelopes = result.envelopes
        if (root.searchMode && !root.gmailSearch && root.searchQuery !== "")
          root.envelopes = Model.fuzzyFilter(result.envelopes, root.searchQuery)
        else
          root.envelopes = result.envelopes

        if (result.error && (
            result.error.indexOf("prompt") >= 0 ||
            result.error.indexOf("TTY") >= 0 ||
            result.error.indexOf("token") >= 0 ||
            result.error.indexOf("auth") >= 0 ||
            result.error.indexOf("credential") >= 0 ||
            result.error.indexOf("401") >= 0 ||
            result.error.indexOf("Unauthorized") >= 0)) {
          root.needsAuth = true
          root.errorMsg = ""
        } else if (result.error && result.error.indexOf("not found") >= 0) {
          root.errorMsg = "himalaya not found — install with 'omarchy pkg add himalaya'"
        } else {
          root.errorMsg = result.error
        }
        root.ready = true
      }
    }
    onExited: function(exitCode) {
      if (!root.ready) {
        root.ready = true
        if (root.errorMsg === "" && !root.needsAuth)
          root.errorMsg = "Failed to load emails"
      }
    }
  }

  Process {
    id: searchProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var raw = String(text || "").trim()
        try {
          var parsed = JSON.parse(raw)
          if (parsed.error) {
            root.errorMsg = parsed.error
            root.envelopes = []
          } else {
            root.envelopes = parsed.envelopes || []
            root.searchNextPage = parsed.next_page || ""
            root.hasMorePages = root.searchNextPage !== ""
            root.errorMsg = ""
          }
          root.ready = true
        } catch (e) {
          root.errorMsg = "Search failed: " + String(e)
          root.envelopes = []
        }
      }
    }
  }

  Process {
    id: authProc
    command: ["python3", Qt.resolvedUrl("auth.py").toString().replace("file://", "")]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var raw = String(text || "").trim()
        if (raw === "OK") {
          root.authInProgress = false
          root.refresh()
        } else {
          root.authInProgress = false
          root.needsAuth = true
          root.errorMsg = raw || "Authentication failed"
        }
      }
    }
    onExited: function(exitCode) {
      root.authInProgress = false
      if (exitCode !== 0) {
        root.needsAuth = true
        if (root.errorMsg === "") root.errorMsg = "Authentication failed"
      } else {
        root.refresh()
      }
    }
  }

  Process {
    id: readProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var raw = String(text || "").trim()
        try {
          var parsed = JSON.parse(raw)
          root.currentDetail = parsed
        } catch (e) {
          root.currentDetail = {
            id: root.selectedId,
            subject: root.selectedEnvelope ? Model.subject(root.selectedEnvelope) : "",
            body: "Failed to read email content: " + String(e),
            error: String(e)
          }
        }
        root.loadingDetail = false
      }
    }
  }

  Process {
    id: flagProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var raw = String(text || "").trim()
        if (raw && raw.indexOf("Successfully") < 0)
          root.errorMsg = raw
      }
    }
    onExited: function(exitCode) {
      if (exitCode === 0) root.localToggleSeen(root.pendingFlagId)
    }
  }

  Process {
    id: moveProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var raw = String(text || "").trim()
        if (raw && raw.indexOf("successfully") < 0)
          root.errorMsg = raw
      }
    }
    onExited: function(exitCode) {
      if (exitCode === 0) root.localRemove(root.pendingMoveId)
    }
  }

  // Auto-refresh every 60s
  Timer {
    id: refreshTimer
    interval: 60000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // IPC
  IpcHandler {
    target: "omarmail"

    function open() { root.openFromHotkey() }
    function close() { root.close() }
    function show() { root.openFromHotkey() }
    function hide() { root.close() }
    function toggle() { root.toggle() }
    function refresh() { root.refresh() }
    function openMessage(id: string) {
      root.openFromHotkey()
      root.selectedId = id
      var found = null
      for (var i = 0; i < root.allEnvelopes.length; i++) {
        if (root.allEnvelopes[i].id === id) {
          found = root.allEnvelopes[i]
          break
        }
      }
      root.selectedEnvelope = found
      root.viewMode = "detail"
      root.readMessage(id)
    }
  }

  // ---- Popup Window

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: false
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(root.viewMode === "detail" ? Style.space(520) : Math.max(Style.space(280), mainContentCol.implicitHeight), Style.space(580))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onCloseRequested: {
        if (root.viewMode === "detail") {
          root.backToInbox()
        } else {
          root.close()
        }
      }

      onTabRequested: function(direction) {
        root.switchPanel(direction)
      }

      onMoveRequested: function(dx, dy) {
        if (root.viewMode === "inbox" && root.envelopes.length > 0) {
          root.cursorActive = true
          if (dy > 0) {
            root.cursorIndex = Math.min(root.envelopes.length - 1, root.cursorIndex + 1)
          } else if (dy < 0) {
            root.cursorIndex = Math.max(0, root.cursorIndex - 1)
          }
        }
      }

      onActivateRequested: {
        if (root.viewMode === "inbox" && root.cursorActive && root.envelopes[root.cursorIndex]) {
          root.openDetail(root.envelopes[root.cursorIndex])
        }
      }

      onTextKey: function(t) {
        if (t === "r" || t === "R") {
          root.refresh()
        } else if (t === "Escape" || t === "Backspace") {
          if (root.viewMode === "detail") root.backToInbox()
          else if (root.searchMode) root.clearSearch()
          else root.close()
        } else if (t === "/" || t === "s") {
          if (root.viewMode === "inbox") {
            root.searchOpen = true
            searchField.forceActiveFocus()
          }
        } else if (t === "u" || t === "U") {
          if (root.viewMode === "detail" && root.selectedEnvelope) {
            root.toggleSeen(root.selectedEnvelope.id, Model.isSeen(root.selectedEnvelope))
          } else if (root.viewMode === "inbox" && root.envelopes[root.cursorIndex]) {
            var env = root.envelopes[root.cursorIndex]
            root.toggleSeen(env.id, Model.isSeen(env))
          }
        } else if (t === "d" || t === "D") {
          if (root.viewMode === "detail" && root.selectedEnvelope) {
            root.deleteMessage(root.selectedEnvelope.id)
          } else if (root.viewMode === "inbox" && root.envelopes[root.cursorIndex]) {
            root.deleteMessage(root.envelopes[root.cursorIndex].id)
          }
        }
      }

      // =========================================================================
      // INBOX LIST VIEW
      // =========================================================================
      Flickable {
        id: inboxScroll
        visible: root.viewMode === "inbox"
        anchors.fill: parent
        contentWidth: width
        contentHeight: mainContentCol.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: mainContentCol
          width: inboxScroll.width
          spacing: Style.space(10)

          // Header Row
          Item {
            width: parent.width
            implicitHeight: Math.max(headerLeft.implicitHeight, headerRight.implicitHeight) + Style.space(4)

            Row {
              id: headerLeft
              anchors.left: parent.left
              anchors.leftMargin: Style.space(12)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(8)

              Text {
                text: "\uf0e0"
                color: Color.accent
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                text: root.gmailSearch ? "Search Results" : (root.searchMode ? "Filtered Inbox" : "Inbox")
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
                anchors.verticalCenter: parent.verticalCenter
              }

              // Unread badge pill
              BorderSurface {
                visible: root.unreadCount > 0 && !root.searchMode
                anchors.verticalCenter: parent.verticalCenter
                implicitWidth: unreadBadgeText.implicitWidth + Style.space(12)
                implicitHeight: unreadBadgeText.implicitHeight + Style.space(4)
                color: Style.selectedFillFor(root.foreground, Color.accent)
                borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
                radius: Style.cornerRadius

                Text {
                  id: unreadBadgeText
                  anchors.centerIn: parent
                  text: root.unreadCount + " unread"
                  color: Color.accent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
              }
            }

            Row {
              id: headerRight
              anchors.right: parent.right
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(4)

              // Search toggle button
              PanelActionButton {
                iconText: "\uf002"
                tooltipText: root.searchOpen ? "Hide search" : "Search emails"
                foreground: root.foreground
                hoverColor: Color.accent
                fontFamily: root.fontFamily
                onClicked: {
                  root.searchOpen = !root.searchOpen
                  if (root.searchOpen) searchField.forceActiveFocus()
                  else if (root.searchMode) root.clearSearch()
                }
              }

              // Refresh button
              PanelActionButton {
                iconText: "\uf021"
                tooltipText: "Refresh inbox"
                foreground: root.foreground
                hoverColor: Color.accent
                fontFamily: root.fontFamily
                onClicked: root.refresh()

                RotationAnimator on rotation {
                  running: listProc.running || searchProc.running
                  from: 0; to: 360
                  duration: 800
                  loops: Animation.Infinite
                }
              }
            }
          }

          // Search Field
          Item {
            visible: root.searchOpen || root.searchMode
            width: parent.width
            implicitHeight: searchField.implicitHeight + Style.space(2)

            TextField {
              id: searchField
              anchors.left: parent.left
              anchors.leftMargin: Style.space(10)
              anchors.right: parent.right
              anchors.rightMargin: Style.space(10)
              placeholderText: "Search subject, sender, or from:..."
              foreground: root.foreground
              accent: Color.accent
              font.pixelSize: Style.font.bodySmall

              onTextChanged: {
                var q = text.trim()
                if (q === "") {
                  if (root.searchMode) root.clearSearch()
                } else if (Model.isGmailOperator(q)) {
                  searchDebounce.restart()
                } else {
                  searchDebounce.stop()
                  root.runSearch(q, "")
                }
              }

              Keys.onEscapePressed: {
                text = ""
                root.clearSearch()
                root.searchOpen = false
              }
            }

            Timer {
              id: searchDebounce
              interval: 600
              onTriggered: {
                var q = searchField.text.trim()
                if (q !== "" && Model.isGmailOperator(q)) root.runSearch(q, "")
              }
            }
          }

          PanelSeparator {
            foreground: root.foreground
          }

          // Auth needed state
          Column {
            visible: root.needsAuth && !root.authInProgress
            width: parent.width - Style.space(32)
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Style.space(12)
            topPadding: Style.space(20)
            bottomPadding: Style.space(20)

            Text {
              text: "\uf0e0"
              color: Color.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
              anchors.horizontalCenter: parent.horizontalCenter
            }

            Text {
              text: "Connect Google Account"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              anchors.horizontalCenter: parent.horizontalCenter
            }

            Text {
              text: "Sign in with Google OAuth in your browser.\nNo passwords stored."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap
              horizontalAlignment: Text.AlignHCenter
              anchors.horizontalCenter: parent.horizontalCenter
            }

            Button {
              anchors.horizontalCenter: parent.horizontalCenter
              text: "Connect Gmail"
              iconText: "\uf090"
              bordered: true
              foreground: Color.accent
              onClicked: root.startAuth()
            }

            Text {
              visible: root.errorMsg !== ""
              text: root.errorMsg
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.Wrap
              horizontalAlignment: Text.AlignHCenter
              width: parent.width
            }
          }

          // Auth in progress state
          Column {
            visible: root.authInProgress
            width: parent.width - Style.space(32)
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Style.space(12)
            topPadding: Style.space(24)
            bottomPadding: Style.space(24)

            Text {
              text: "\uf110"
              color: Color.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
              anchors.horizontalCenter: parent.horizontalCenter

              RotationAnimator on rotation {
                running: root.authInProgress
                from: 0; to: 360
                duration: 1000
                loops: Animation.Infinite
              }
            }

            Text {
              text: "Waiting for browser login..."
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
              anchors.horizontalCenter: parent.horizontalCenter
            }

            Text {
              text: "Complete the sign-in in your browser window."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              horizontalAlignment: Text.AlignHCenter
              anchors.horizontalCenter: parent.horizontalCenter
            }
          }

          // General Error
          Item {
            visible: root.ready && root.errorMsg !== "" && !root.needsAuth && !root.authInProgress
            width: parent.width
            implicitHeight: errText.implicitHeight + Style.space(24)

            Text {
              id: errText
              anchors.centerIn: parent
              width: parent.width - Style.space(32)
              text: root.errorMsg
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap
              horizontalAlignment: Text.AlignHCenter
            }
          }

          // Loading inbox state
          Column {
            visible: !root.ready && !root.needsAuth && !root.authInProgress
            width: parent.width
            spacing: Style.space(12)
            topPadding: Style.space(32)
            bottomPadding: Style.space(32)

            Text {
              text: "\uf110"
              color: Color.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              anchors.horizontalCenter: parent.horizontalCenter

              RotationAnimator on rotation {
                running: !root.ready
                from: 0; to: 360
                duration: 900
                loops: Animation.Infinite
              }
            }

            Text {
              text: "Loading inbox..."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              anchors.horizontalCenter: parent.horizontalCenter
            }
          }

          // Empty Inbox
          Column {
            visible: root.ready && root.envelopes.length === 0 && root.errorMsg === "" && !root.needsAuth && !root.authInProgress
            width: parent.width
            spacing: Style.space(8)
            topPadding: Style.space(24)
            bottomPadding: Style.space(24)

            Text {
              text: "\uf00c"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              anchors.horizontalCenter: parent.horizontalCenter
            }

            Text {
              text: root.searchMode ? "No matching emails found" : "All caught up! Inbox is clear."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              anchors.horizontalCenter: parent.horizontalCenter
            }
          }

          // Email Rows List
          Repeater {
            model: root.envelopes
            delegate: CursorSurface {
              id: emailRow
              required property var modelData
              required property int index

              readonly property var env: modelData
              readonly property bool isSeen: Model.isSeen(env)
              readonly property bool isHovered: rowHover.hovered

              hasCursor: root.cursorActive && root.cursorIndex === index
              foreground: root.foreground
              accent: Color.accent
              width: mainContentCol.width
              implicitHeight: rowLayout.implicitHeight + Style.space(12)

              HoverHandler {
                id: rowHover
                onHoveredChanged: {
                  if (hovered) {
                    root.cursorActive = true
                    root.cursorIndex = emailRow.index
                  }
                }
              }

              MouseArea {
                id: rowMouse
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.right: actionRow.visible ? actionRow.left : parent.right
                cursorShape: Qt.PointingHandCursor
                onClicked: root.openDetail(emailRow.env)
              }

              Row {
                id: rowLayout
                anchors.left: parent.left
                anchors.leftMargin: Style.space(12)
                anchors.right: parent.right
                anchors.rightMargin: Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(10)

                // Unread dot indicator
                Item {
                  width: Style.space(8)
                  height: Style.space(8)
                  anchors.verticalCenter: parent.verticalCenter

                  Rectangle {
                    visible: !emailRow.isSeen
                    anchors.centerIn: parent
                    width: Style.space(6)
                    height: Style.space(6)
                    radius: width / 2
                    color: Color.accent
                  }
                }

                // Avatar initials circle
                BorderSurface {
                  anchors.verticalCenter: parent.verticalCenter
                  implicitWidth: Style.space(28)
                  implicitHeight: Style.space(28)
                  radius: Style.cornerRadius
                  color: emailRow.isSeen ? Style.hoverFillFor(root.foreground, Color.accent) : Style.selectedFillFor(root.foreground, Color.accent)
                  borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)

                  Text {
                    anchors.centerIn: parent
                    text: Model.senderInitials(emailRow.env)
                    color: emailRow.isSeen ? root.dim : root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }

                // Sender, Subject, Date
                Column {
                  width: parent.width - Style.space(46) - (emailRow.isHovered ? actionRow.implicitWidth + Style.space(8) : Style.space(8))
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.space(2)

                  Row {
                    width: parent.width
                    spacing: Style.space(6)

                    Text {
                      text: Model.senderName(emailRow.env)
                      color: emailRow.isSeen ? root.dim : root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                      font.bold: !emailRow.isSeen
                      elide: Text.ElideRight
                      width: parent.width - dateText.implicitWidth - Style.space(6)
                    }

                    Text {
                      id: dateText
                      text: Model.formatDate(emailRow.env.date)
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      horizontalAlignment: Text.AlignRight
                    }
                  }

                  Text {
                    width: parent.width
                    text: Model.subject(emailRow.env)
                    color: emailRow.isSeen ? root.dim : root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    font.bold: !emailRow.isSeen
                    elide: Text.ElideRight
                  }
                }

                // Row action buttons (isolated from row click)
                Row {
                  id: actionRow
                  z: 10
                  visible: emailRow.isHovered
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.space(4)

                  PanelActionButton {
                    id: seenBtn
                    iconText: emailRow.isSeen ? "\uf0e0" : "\uf2b7"
                    tooltipText: emailRow.isSeen ? "Mark as unread" : "Mark as read"
                    foreground: root.foreground
                    hoverColor: Color.accent
                    fontFamily: root.fontFamily
                    onClicked: {
                      root.toggleSeen(emailRow.env.id, emailRow.isSeen)
                    }
                  }

                  PanelActionButton {
                    id: trashBtn
                    iconText: "\uf014"
                    tooltipText: "Move to trash"
                    foreground: root.foreground
                    hoverColor: root.urgent
                    fontFamily: root.fontFamily
                    onClicked: {
                      root.deleteMessage(emailRow.env.id)
                    }
                  }
                }
              }
            }
          }

          // Pagination Bar
          Item {
            visible: !root.needsAuth && !root.authInProgress && root.envelopes.length > 0
            width: parent.width
            implicitHeight: pagRow.implicitHeight + Style.space(8)

            Row {
              id: pagRow
              anchors.centerIn: parent
              spacing: Style.space(12)

              PanelActionButton {
                iconText: "\uf053"
                tooltipText: "Previous page"
                foreground: root.foreground
                hoverColor: Color.accent
                fontFamily: root.fontFamily
                enabled: root.currentPage > 1 && !root.searchMode
                opacity: enabled ? 1.0 : 0.35
                onClicked: root.prevPage()
              }

              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root.searchMode ? (root.envelopes.length + " results") : ("Page " + root.currentPage)
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
              }

              PanelActionButton {
                iconText: "\uf054"
                tooltipText: "Next page"
                foreground: root.foreground
                hoverColor: Color.accent
                fontFamily: root.fontFamily
                enabled: root.searchMode ? root.hasMorePages : root.envelopes.length >= root.pageSize
                opacity: enabled ? 1.0 : 0.35
                onClicked: root.nextPage()
              }
            }
          }
        }
      }

      // =========================================================================
      // EMAIL DETAIL VIEW (First-Class Full Reader)
      // =========================================================================
      Column {
        id: detailView
        visible: root.viewMode === "detail"
        anchors.fill: parent
        spacing: Style.space(8)

        // Top Navigation Bar
        Item {
          id: detailTopNav
          width: parent.width
          implicitHeight: Math.max(backBtn.implicitHeight, detailActions.implicitHeight) + Style.space(4)

          Button {
            id: backBtn
            anchors.left: parent.left
            anchors.leftMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            text: "Inbox"
            iconText: "\uf060"
            fontFamily: root.fontFamily
            fontSize: Style.font.bodySmall
            bordered: false
            foreground: root.foreground
            accent: Color.accent
            onClicked: root.backToInbox()
          }

          Row {
            id: detailActions
            anchors.right: parent.right
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(4)

            // Toggle Seen
            PanelActionButton {
              iconText: (root.selectedEnvelope && Model.isSeen(root.selectedEnvelope)) ? "\uf0e0" : "\uf2b7"
              tooltipText: (root.selectedEnvelope && Model.isSeen(root.selectedEnvelope)) ? "Mark as unread" : "Mark as read"
              foreground: root.foreground
              hoverColor: Color.accent
              fontFamily: root.fontFamily
              onClicked: {
                if (root.selectedEnvelope) {
                  root.toggleSeen(root.selectedEnvelope.id, Model.isSeen(root.selectedEnvelope))
                }
              }
            }

            // Move to Trash
            PanelActionButton {
              iconText: "\uf014"
              tooltipText: "Delete email"
              foreground: root.foreground
              hoverColor: root.urgent
              fontFamily: root.fontFamily
              onClicked: {
                if (root.selectedEnvelope) {
                  root.deleteMessage(root.selectedEnvelope.id)
                }
              }
            }

            // Open in Gmail Web
            PanelActionButton {
              iconText: "\uf08e"
              tooltipText: "Open in Gmail Web"
              foreground: root.foreground
              hoverColor: Color.accent
              fontFamily: root.fontFamily
              onClicked: {
                if (root.selectedId) root.openInGmail(root.selectedId)
              }
            }
          }
        }

        PanelSeparator {
          id: sep1
          foreground: root.foreground
        }

        // Email Header Card
        Column {
          id: detailHeaderCard
          width: parent.width - Style.space(24)
          anchors.horizontalCenter: parent.horizontalCenter
          spacing: Style.space(6)

          // Subject Title
          Text {
            width: parent.width
            text: root.currentDetail ? root.currentDetail.subject : (root.selectedEnvelope ? Model.subject(root.selectedEnvelope) : "")
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true
            wrapMode: Text.Wrap
          }

          // Sender Info Row
          Row {
            width: parent.width
            spacing: Style.space(8)

            // Avatar
            BorderSurface {
              anchors.verticalCenter: parent.verticalCenter
              implicitWidth: Style.space(32)
              implicitHeight: Style.space(32)
              radius: Style.cornerRadius
              color: Style.selectedFillFor(root.foreground, Color.accent)
              borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)

              Text {
                anchors.centerIn: parent
                text: root.currentDetail && root.currentDetail.from_initials ? root.currentDetail.from_initials : (root.selectedEnvelope ? Model.senderInitials(root.selectedEnvelope) : "?")
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                font.bold: true
              }
            }

            Column {
              anchors.verticalCenter: parent.verticalCenter
              width: parent.width - Style.space(40)
              spacing: Style.space(1)

              Row {
                width: parent.width
                spacing: Style.space(6)

                Text {
                  text: root.currentDetail && root.currentDetail.from_name ? root.currentDetail.from_name : (root.selectedEnvelope ? Model.senderName(root.selectedEnvelope) : "Unknown")
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  text: {
                    var em = root.currentDetail && root.currentDetail.from_email ? root.currentDetail.from_email : (root.selectedEnvelope ? Model.senderEmail(root.selectedEnvelope) : "")
                    return em ? "<" + em + ">" : ""
                  }
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              // Date & Recipients
              Row {
                width: parent.width
                spacing: Style.space(8)

                Text {
                  text: root.currentDetail && root.currentDetail.date_formatted ? root.currentDetail.date_formatted : (root.selectedEnvelope ? Model.formatFullDate(root.selectedEnvelope.date) : "")
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }

                // Attachments badge
                Row {
                  visible: root.currentDetail && root.currentDetail.has_attachments
                  spacing: Style.space(4)
                  anchors.verticalCenter: parent.verticalCenter

                  Text {
                    text: "\uf0c6"
                    color: Color.accent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }

                  Text {
                    text: "Attachment"
                    color: Color.accent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }
                }
              }
            }
          }
        }

        PanelSeparator {
          id: sep2
          foreground: root.foreground
        }

        // Scrollable Email Content Body
        Item {
          id: detailBodyContainer
          width: parent.width
          height: Math.max(Style.space(220), keyCatcher.height - detailTopNav.implicitHeight - sep1.implicitHeight - detailHeaderCard.implicitHeight - sep2.implicitHeight - detailView.spacing * 5)
          clip: true

          // Loading spinner
          Column {
            visible: root.loadingDetail
            anchors.centerIn: parent
            spacing: Style.space(8)

            Text {
              text: "\uf110"
              color: Color.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
              anchors.horizontalCenter: parent.horizontalCenter

              RotationAnimator on rotation {
                running: root.loadingDetail
                from: 0; to: 360
                duration: 900
                loops: Animation.Infinite
              }
            }

            Text {
              text: "Loading email content..."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              anchors.horizontalCenter: parent.horizontalCenter
            }
          }

          // Rendered Markdown Body
          Flickable {
            id: bodyScroll
            visible: !root.loadingDetail
            anchors.fill: parent
            contentWidth: width
            contentHeight: bodyContentCol.implicitHeight + Style.space(20)
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Column {
              id: bodyContentCol
              width: bodyScroll.width - Style.space(24)
              anchors.horizontalCenter: parent.horizontalCenter
              spacing: Style.space(10)
              topPadding: Style.space(4)
              bottomPadding: Style.space(16)

              TextEdit {
                id: bodyRichText
                width: parent.width
                text: root.currentDetail ? (root.currentDetail.body_html || root.currentDetail.body || "(No message text)") : ""
                textFormat: TextEdit.RichText
                readOnly: true
                selectByMouse: true
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: TextEdit.Wrap
                onLinkActivated: function(link) {
                  Qt.openUrlExternally(link)
                }
              }
            }
          }
        }
      }
    }
  }
}


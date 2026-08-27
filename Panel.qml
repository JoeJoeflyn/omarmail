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
  // Panel size from shell.json entry settings (panelWidth/panelHeight), clamped to safe ranges
  readonly property int panelWidthSetting: {
    var v = settings && settings.panelWidth !== undefined ? Number(settings.panelWidth) : 720
    return Style.space(Math.max(320, Math.min(1200, v)))
  }
  readonly property int panelHeightSetting: {
    var v = settings && settings.panelHeight !== undefined ? Number(settings.panelHeight) : 620
    return Style.space(Math.max(300, Math.min(900, v)))
  }

  // ---- State
  property string viewMode: "inbox"
  property var envelopes: []
  property var allEnvelopes: []
  property string errorMsg: ""
  property bool ready: false
  property bool authInProgress: false
  property bool needsAuth: false

  property string selectedId: ""
  property var selectedEnvelope: null
  property var currentDetail: null
  property bool loadingDetail: false

  property string searchQuery: ""
  property bool searchMode: false
  property bool searchOpen: false
  property bool gmailSearch: false
  property int currentPage: 1
  property int pageSize: 10
  property string searchNextPage: ""
  property bool hasMorePages: false

  property var excludedTerms: []

  property int cursorIndex: 0
  property bool cursorActive: false

  property string pendingFlagId: ""
  property string pendingMoveId: ""
  property int envelopesRevision: 0
  property var seenOverrides: ({})
  property var deletedIds: ({})

  readonly property int unreadCount: {
    var rev = envelopesRevision
    var list = allEnvelopes.length > 0 ? allEnvelopes : envelopes
    var count = 0
    for (var i = 0; i < list.length; i++) {
      var item = list[i]
      if (item && item.id && !root.deletedIds[item.id]) {
        var isS = (item.id in seenOverrides) ? seenOverrides[item.id] : Model.isSeen(item)
        if (!isS) count++
      }
    }
    return count
  }
  property string label: "\uf0e0"

  // Exposed for components
  readonly property bool listProcRunning: listProc.running
  readonly property bool searchProcRunning: searchProc.running
  property int keyCatcherHeight: 0

  Component.onCompleted: { cacheInitProc.running = true; loadExcludedTerms() }

  // ---- Lifecycle
  function open() {
    openedFromHotkey = false
    setCenterHoverRevealSuppressed(false)
    root.controller.show()
    root.refresh()
  }

  function openFromHotkey() {
    openedFromHotkey = true
    root.controller.show()
    root.refresh()
    Qt.callLater(function() { if (root.opened) setCenterHoverRevealSuppressed(true) })
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
  function fetchPage(page, force) {
    currentPage = page
    listProc.running = false
    var cmd = ["python3", Qt.resolvedUrl("list.py").toString().replace("file://", ""), String(pageSize), String(currentPage)]
    if (force) cmd.push("--force")
    listProc.command = cmd
    listProc.running = true
  }

  function refresh() {
    if (searchMode && !gmailSearch && searchQuery !== "") {
      fetchPage(1, true)
    } else if (gmailSearch) {
      runSearch(searchQuery, "")
    } else {
      fetchPage(currentPage, true)
    }
    if (viewMode === "detail" && selectedId !== "") readMessage(selectedId)
  }

  function loadExcludedTerms() {
    filterTermsProc.command = ["python3", Qt.resolvedUrl("list.py").toString().replace("file://", ""), "--get-excluded"]
    filterTermsProc.running = true
  }

  function toggleCategory(term, checked) {
    var terms = excludedTerms.slice()
    if (checked && terms.indexOf(term) < 0) terms.push(term)
    else if (!checked) terms = terms.filter(function(t) { return t !== term })
    excludedTerms = terms
    filterSetProc.command = ["python3", Qt.resolvedUrl("list.py").toString().replace("file://", ""), "--set-excluded", JSON.stringify(terms)]
    filterSetProc.running = true
  }

  function runSearch(query, pageToken) {
    searchQuery = query; searchMode = true; errorMsg = ""
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
    searchMode = false; gmailSearch = false; searchQuery = ""
    searchNextPage = ""; hasMorePages = false; currentPage = 1
    envelopes = allEnvelopes
  }

  function nextPage() {
    if (gmailSearch) { if (searchNextPage !== "") runSearch(searchQuery, searchNextPage) }
    else { fetchPage(currentPage + 1) }
  }

  function prevPage() {
    if (gmailSearch) return
    if (currentPage > 1) { fetchPage(currentPage - 1) }
  }

  function openDetail(env) {
    if (!env || !env.id) return
    var wasUnseen = !Model.isSeen(env)
    selectedId = env.id
    selectedEnvelope = Object.assign({}, env)
    viewMode = "detail"
    readMessage(env.id)
    if (wasUnseen) {
      localSetSeen(env.id, true)
      markRead(env.id)
    }
  }

  function backToInbox() {
    viewMode = "inbox"; selectedId = ""; selectedEnvelope = null; currentDetail = null; loadingDetail = false
  }

  function readMessage(id) {
    loadingDetail = true; readProc.running = false
    readProc.command = ["python3", Qt.resolvedUrl("read.py").toString().replace("file://", ""), id]
    readProc.running = true
  }

  function markRead(id) {
    pendingFlagId = id
    localSetSeen(id, true)
    flagProc.running = false
    flagProc.command = ["python3", Qt.resolvedUrl("action.py").toString().replace("file://", ""), "mark_read", id]
    flagProc.running = true
  }

  function markUnread(id) {
    pendingFlagId = id
    localSetSeen(id, false)
    flagProc.running = false
    flagProc.command = ["python3", Qt.resolvedUrl("action.py").toString().replace("file://", ""), "mark_unread", id]
    flagProc.running = true
  }

  function toggleSeen(id, currentSeen) { if (currentSeen) markUnread(id); else markRead(id) }

  function deleteMessage(id) {
    pendingMoveId = id
    var wasInDetail = viewMode === "detail" && selectedId === id
    localRemove(id)
    moveProc.command = ["python3", Qt.resolvedUrl("action.py").toString().replace("file://", ""), "delete", id]
    moveProc.running = true
    if (wasInDetail) backToInbox()
  }

  function openInGmail(id) { Qt.openUrlExternally("https://mail.google.com/mail/u/0/#inbox/" + id) }

  function startAuth() { authInProgress = true; needsAuth = false; errorMsg = ""; authProc.running = true }

  function isEnvelopeSeen(id) {
    if (!id || root.deletedIds[id]) return true
    if (id in seenOverrides) return seenOverrides[id]
    var rev = envelopesRevision
    for (var i = 0; i < allEnvelopes.length; i++) {
      if (allEnvelopes[i].id === id) return Model.isSeen(allEnvelopes[i])
    }
    return true
  }

  function getEnvelope(id) {
    if (!id || root.deletedIds[id]) return null
    var rev = envelopesRevision
    for (var i = 0; i < allEnvelopes.length; i++) {
      if (allEnvelopes[i].id === id) return allEnvelopes[i]
    }
    return null
  }

  function localSetSeen(id, seen) {
    var so = Object.assign({}, seenOverrides)
    so[id] = seen
    seenOverrides = so

    function updateList(list) {
      var updated = []
      for (var i = 0; i < list.length; i++) {
        var env = Object.assign({}, list[i])
        if (env.id === id) {
          var flags = env.flags ? [].concat(env.flags) : []
          flags = flags.filter(function(f) {
            var n = typeof f === "string" ? f.toLowerCase() : (f && f.iana ? String(f.iana).toLowerCase() : (f && f.raw ? String(f.raw).replace("\\", "").toLowerCase() : ""))
            return n !== "seen"
          })
          if (seen) {
            flags.push({raw: "\\Seen", iana: "seen"})
          }
          env.flags = flags
        }
        updated.push(env)
      }
      return updated
    }

    allEnvelopes = updateList(allEnvelopes)
    envelopes = searchMode && !gmailSearch ? Model.fuzzyFilter(allEnvelopes, searchQuery) : updateList(envelopes)

    if (selectedEnvelope && selectedEnvelope.id === id) {
      var sFlags = selectedEnvelope.flags ? [].concat(selectedEnvelope.flags) : []
      sFlags = sFlags.filter(function(f) {
        var n = typeof f === "string" ? f.toLowerCase() : (f && f.iana ? String(f.iana).toLowerCase() : (f && f.raw ? String(f.raw).replace("\\", "").toLowerCase() : ""))
        return n !== "seen"
      })
      if (seen) {
        sFlags.push({raw: "\\Seen", iana: "seen"})
      }
      var newSel = Object.assign({}, selectedEnvelope)
      newSel.flags = sFlags
      selectedEnvelope = newSel
    }
    envelopesRevision++
  }

  function localToggleSeen(id) {
    var current = false
    for (var i = 0; i < allEnvelopes.length; i++) {
      if (allEnvelopes[i].id === id) { current = Model.isSeen(allEnvelopes[i]); break }
    }
    localSetSeen(id, !current)
  }

  function localRemove(id) {
    var dids = Object.assign({}, deletedIds)
    dids[id] = true
    deletedIds = dids

    allEnvelopes = allEnvelopes.filter(function(env) { return env.id !== id })
    envelopes = envelopes.filter(function(env) { return env.id !== id })
    if (selectedId === id) {
      selectedId = ""
      selectedEnvelope = null
      currentDetail = null
    }
    envelopesRevision++
  }

  // ---- Processes
  Process {
    id: cacheInitProc
    command: ["python3", Qt.resolvedUrl("list.py").toString().replace("file://", ""), "--cache-only"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(String(text || "").trim())
          if (parsed && parsed.envelopes && parsed.envelopes.length > 0 && root.allEnvelopes.length === 0) {
            var validEnvs = []
            for (var i = 0; i < parsed.envelopes.length; i++) {
              var env = parsed.envelopes[i]
              if (!env || !env.id || root.deletedIds[env.id]) continue
              if (env.id in root.seenOverrides) {
                var seen = root.seenOverrides[env.id]
                var flags = env.flags ? [].concat(env.flags) : []
                flags = flags.filter(function(f) {
                  var n = typeof f === "string" ? f.toLowerCase() : (f && f.iana ? String(f.iana).toLowerCase() : (f && f.raw ? String(f.raw).replace("\\", "").toLowerCase() : ""))
                  return n !== "seen"
                })
                if (seen) flags.push({raw: "\\Seen", iana: "seen"})
                env.flags = flags
              }
              validEnvs.push(env)
            }
            root.allEnvelopes = validEnvs
            if (!root.searchMode) root.envelopes = validEnvs
            root.envelopesRevision++
            root.ready = true
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: listProc
    command: ["python3", Qt.resolvedUrl("list.py").toString().replace("file://", ""), String(pageSize), String(currentPage)]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var result = Model.parseEnvelopeList(String(text || "").trim())
        if (result.envelopes && result.envelopes.length > 0) {
          var validEnvs = []
          for (var i = 0; i < result.envelopes.length; i++) {
            var env = result.envelopes[i]
            if (!env || !env.id || root.deletedIds[env.id]) continue
            if (env.id in root.seenOverrides) {
              var seen = root.seenOverrides[env.id]
              var flags = env.flags ? [].concat(env.flags) : []
              flags = flags.filter(function(f) {
                var n = typeof f === "string" ? f.toLowerCase() : (f && f.iana ? String(f.iana).toLowerCase() : (f && f.raw ? String(f.raw).replace("\\", "").toLowerCase() : ""))
                return n !== "seen"
              })
              if (seen) flags.push({raw: "\\Seen", iana: "seen"})
              env.flags = flags
            }
            validEnvs.push(env)
          }
          root.allEnvelopes = validEnvs
          root.envelopes = (root.searchMode && !root.gmailSearch && root.searchQuery !== "") ? Model.fuzzyFilter(validEnvs, root.searchQuery) : validEnvs
          root.envelopesRevision++
        }
        if (result.error && (result.error.indexOf("prompt") >= 0 || result.error.indexOf("TTY") >= 0 || result.error.indexOf("token") >= 0 || result.error.indexOf("auth") >= 0 || result.error.indexOf("credential") >= 0 || result.error.indexOf("401") >= 0 || result.error.indexOf("Unauthorized") >= 0)) {
          root.needsAuth = true; root.errorMsg = ""
        } else if (result.error && result.error.indexOf("not found") >= 0) {
          root.errorMsg = "himalaya not found — install with 'omarchy pkg add himalaya'"
        } else if (result.error && root.allEnvelopes.length === 0) {
          root.errorMsg = result.error
        }
        root.ready = true
      }
    }
    onExited: function(exitCode) {
      if (!root.ready) { root.ready = true; if (root.errorMsg === "" && !root.needsAuth && root.allEnvelopes.length === 0) root.errorMsg = "Failed to load emails" }
    }
  }

  Process {
    id: searchProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(String(text || "").trim())
          if (parsed.error) { root.errorMsg = parsed.error; root.envelopes = [] }
          else {
            var rawSearchEnvs = parsed.envelopes || []
            root.envelopes = rawSearchEnvs.filter(function(env) { return env && env.id && !root.deletedIds[env.id] })
            root.searchNextPage = parsed.next_page || ""
            root.hasMorePages = root.searchNextPage !== ""
            root.errorMsg = ""
          }
          root.ready = true
        } catch (e) { root.errorMsg = "Search failed: " + String(e); root.envelopes = [] }
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
        if (raw === "OK") { root.authInProgress = false; root.refresh() }
        else { root.authInProgress = false; root.needsAuth = true; root.errorMsg = raw || "Authentication failed" }
      }
    }
    onExited: function(exitCode) {
      root.authInProgress = false
      if (exitCode !== 0) { root.needsAuth = true; if (root.errorMsg === "") root.errorMsg = "Authentication failed" }
      else root.refresh()
    }
  }

  Process {
    id: readProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.currentDetail = JSON.parse(String(text || "").trim()) }
        catch (e) {
          root.currentDetail = { id: root.selectedId, subject: root.selectedEnvelope ? Model.subject(root.selectedEnvelope) : "", body: "Failed to read email content: " + String(e), error: String(e) }
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
        try {
          var res = JSON.parse(String(text || "{}"))
          if (!res.success && res.error) root.errorMsg = res.error
        } catch (e) {}
      }
    }
  }

  Process {
    id: moveProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var res = JSON.parse(String(text || "{}"))
          if (!res.success && res.error) root.errorMsg = res.error
        } catch (e) {}
      }
    }
  }

  Timer { id: refreshTimer; interval: 60000; running: true; repeat: true; triggeredOnStart: true; onTriggered: root.refresh() }

  Process {
    id: filterTermsProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.excludedTerms = JSON.parse(String(text || "{}")).terms || [] }
        catch (e) {}
      }
    }
  }

  Process {
    id: filterSetProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: { root.refresh() }
    }
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
      root.openFromHotkey(); root.selectedId = id
      var found = null
      for (var i = 0; i < root.allEnvelopes.length; i++) { if (root.allEnvelopes[i].id === id) { found = root.allEnvelopes[i]; break } }
      root.selectedEnvelope = found; root.viewMode = "detail"; root.readMessage(id)
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
    contentWidth: panel.fittedContentWidth(root.panelWidthSetting)
    contentHeight: root.viewMode === "detail"
      ? panel.fittedContentHeight(Style.space(680), Style.space(800))
      : panel.fittedContentHeight(Math.max(Style.space(360), Math.min(mainContentCol.contentHeight + Style.space(16), root.panelHeightSetting)), root.panelHeightSetting)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      Component.onCompleted: root.keyCatcherHeight = keyCatcher.height
      onHeightChanged: root.keyCatcherHeight = keyCatcher.height

      onCloseRequested: { if (root.viewMode === "detail") root.backToInbox(); else root.close() }
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (root.viewMode === "inbox" && root.envelopes.length > 0) {
          root.cursorActive = true
          if (dy > 0) root.cursorIndex = Math.min(root.envelopes.length - 1, root.cursorIndex + 1)
          else if (dy < 0) root.cursorIndex = Math.max(0, root.cursorIndex - 1)
        }
      }
      onActivateRequested: {
        if (root.viewMode === "inbox" && root.cursorActive && root.envelopes[root.cursorIndex]) root.openDetail(root.envelopes[root.cursorIndex])
      }
      onTextKey: function(t) {
        if (t === "r" || t === "R") root.refresh()
        else if (t === "Escape" || t === "Backspace") { if (root.viewMode === "detail") root.backToInbox(); else if (root.searchMode) root.clearSearch(); else root.close() }
        else if (t === "/" || t === "s") { if (root.viewMode === "inbox") { root.searchOpen = true; mainContentCol.searchField.forceActiveFocus() } }
        else if (t === "u" || t === "U") {
          if (root.viewMode === "detail" && root.selectedEnvelope) root.toggleSeen(root.selectedEnvelope.id, root.isEnvelopeSeen(root.selectedEnvelope.id))
          else if (root.viewMode === "inbox" && root.envelopes[root.cursorIndex]) root.toggleSeen(root.envelopes[root.cursorIndex].id, root.isEnvelopeSeen(root.envelopes[root.cursorIndex].id))
        }
        else if (t === "d" || t === "D") {
          if (root.viewMode === "detail" && root.selectedEnvelope) root.deleteMessage(root.selectedEnvelope.id)
          else if (root.viewMode === "inbox" && root.envelopes[root.cursorIndex]) root.deleteMessage(root.envelopes[root.cursorIndex].id)
        }
        else if (t === "o" || t === "O") {
          if (root.viewMode === "detail" && root.selectedId) root.openInGmail(root.selectedId)
        }
        else if (t === "n" || t === "N" || t === "]") {
          if (root.viewMode === "inbox") root.nextPage()
        }
        else if (t === "p" || t === "P" || t === "[") {
          if (root.viewMode === "inbox") root.prevPage()
        }
      }

      InboxView { id: mainContentCol; p: root }
      DetailView { p: root }
    }
  }
}

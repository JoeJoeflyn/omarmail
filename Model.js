// Pure JS helpers for parsing himalaya JSON output.
// Handles both Maildir (flags as strings) and Gmail (flags as objects).

function normalizeFlags(flags) {
  if (!flags) return []
  var result = []
  for (var i = 0; i < flags.length; i++) {
    var f = flags[i]
    if (typeof f === "string") result.push(f.toLowerCase())
    else if (f && f.iana) result.push(String(f.iana).toLowerCase())
    else if (f && f.raw) result.push(String(f.raw).replace("\\", "").toLowerCase())
  }
  return result
}

function isSeen(envelope) {
  if (!envelope || !envelope.flags) return false
  return normalizeFlags(envelope.flags).indexOf("seen") >= 0
}

function isFlagged(envelope) {
  if (!envelope || !envelope.flags) return false
  return normalizeFlags(envelope.flags).indexOf("flagged") >= 0
}

function senderName(envelope) {
  if (!envelope || !envelope.from || !envelope.from.length) return "Unknown"
  var f = envelope.from[0]
  if (f.name && String(f.name).trim() !== "") return String(f.name).trim()
  if (f.email) return String(f.email).split("@")[0]
  return "Unknown"
}

function senderEmail(envelope) {
  if (!envelope || !envelope.from || !envelope.from.length) return ""
  return String(envelope.from[0].email || "").trim()
}

function senderInitials(envelope) {
  var name = senderName(envelope)
  if (!name) return "?"
  var parts = name.trim().split(/\s+/)
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
  }
  return name.slice(0, 2).toUpperCase()
}

function subject(envelope) {
  if (!envelope) return "(No Subject)"
  var sub = envelope.subject || ""
  return sub.trim() !== "" ? sub : "(No Subject)"
}

function formatRecipients(list) {
  if (!list || !list.length) return ""
  var names = []
  for (var i = 0; i < list.length; i++) {
    var item = list[i]
    if (!item) continue
    if (item.name && item.name !== item.email) names.push(item.name + " <" + item.email + ">")
    else if (item.email) names.push(item.email)
  }
  return names.join(", ")
}

function formatDate(dateStr) {
  if (!dateStr) return ""
  var d = new Date(dateStr)
  if (isNaN(d.getTime())) return dateStr
  var now = new Date()
  if (d.toDateString() === now.toDateString()) {
    var h = d.getHours()
    var m = d.getMinutes()
    return (h < 10 ? "0" + h : h) + ":" + (m < 10 ? "0" + m : m)
  }
  var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  if (d.getFullYear() === now.getFullYear()) {
    return months[d.getMonth()] + " " + d.getDate()
  }
  return months[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear()
}

function formatFullDate(dateStr) {
  if (!dateStr) return ""
  var d = new Date(dateStr)
  if (isNaN(d.getTime())) return dateStr
  var days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
  var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  var h = d.getHours()
  var m = d.getMinutes()
  var timeStr = (h < 10 ? "0" + h : h) + ":" + (m < 10 ? "0" + m : m)
  return days[d.getDay()] + ", " + d.getDate() + " " + months[d.getMonth()] + " " + d.getFullYear() + " at " + timeStr
}

function unreadCount(envelopes) {
  var count = 0
  if (!envelopes) return count
  for (var i = 0; i < envelopes.length; i++) {
    if (!isSeen(envelopes[i])) count++
  }
  return count
}

function parseEnvelopeList(raw) {
  if (!raw) return { envelopes: [], error: "No output" }
  try {
    var parsed = JSON.parse(raw)
    if (parsed.error) return { envelopes: [], error: parsed.error }
    if (Array.isArray(parsed)) return { envelopes: parsed, error: "" }
    if (parsed.envelopes && Array.isArray(parsed.envelopes)) return { envelopes: parsed.envelopes, error: "" }
    if (parsed.response && Array.isArray(parsed.response)) return { envelopes: parsed.response, error: "" }
    return { envelopes: [], error: "" }
  } catch (e) {
    return { envelopes: [], error: "Failed to parse: " + String(e) }
  }
}

// Fuzzy match: returns true if query matches text with characters in order
// but not necessarily contiguous. Case-insensitive. Subsequence matching.
function fuzzyMatch(text, query) {
  if (!query) return true
  if (!text) return false
  text = text.toLowerCase()
  query = query.toLowerCase()

  if (text.indexOf(query) >= 0) return true

  var ti = 0
  for (var qi = 0; qi < query.length; qi++) {
    ti = text.indexOf(query[qi], ti)
    if (ti < 0) return false
    ti++
  }
  return true
}

function fuzzyFilter(envelopes, query) {
  if (!query || !envelopes) return envelopes
  var result = []
  for (var i = 0; i < envelopes.length; i++) {
    var env = envelopes[i]
    var haystack = subject(env) + " " + senderName(env) + " " + senderEmail(env)
    if (fuzzyMatch(haystack, query)) result.push(env)
  }
  return result
}

function isGmailOperator(query) {
  if (!query) return false
  var ops = ["from:", "to:", "subject:", "is:", "has:", "label:", "before:", "after:", "older:", "newer:", "cc:", "bcc:"]
  var lower = query.toLowerCase().trim()
  for (var i = 0; i < ops.length; i++) {
    if (lower.indexOf(ops[i]) === 0) return true
  }
  return false
}


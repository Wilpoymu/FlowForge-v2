var BRIDGE_PORT = 5556;
var WS_PORT = 5557;
var BRIDGE_BASE = "http://127.0.0.1:" + BRIDGE_PORT;
var WS_URL = "ws://127.0.0.1:" + WS_PORT;
var POLL_INTERVAL_MS = 1000;
var pollErrors = 0;
var activeRequests = 0;
var MAX_CONCURRENT = 10;
var _flowAccountHash = null;
var _ws = null;
var _wsConnected = false;
var _wsReconnectTimer = null;

function wsConnect() {
  if (_ws && (_ws.readyState === WebSocket.CONNECTING || _ws.readyState === WebSocket.OPEN)) return;
  try {
    _ws = new WebSocket(WS_URL);
  } catch (e) {
    return;
  }
  _ws.onopen = function() {
    _wsConnected = true;
    console.log("[Imperio] WS connected");
    if (_flowAccountHash) {
      _ws.send(JSON.stringify({type: "register", account_hash: _flowAccountHash}));
      console.log("[Imperio] WS registered account " + _flowAccountHash);
    }
  };
  _ws.onmessage = function(event) {
    try {
      var msg = JSON.parse(event.data);
      if (msg.type === "generate" && msg.requests) {
        for (var i = 0; i < msg.requests.length; i++) {
          activeRequests++;
          console.log("[Imperio] WS got request: " + msg.requests[i].requestId + " (active=" + activeRequests + ")");
          window.postMessage({
            type: "FLOW_GENERATE_REQUEST",
            requestId: msg.requests[i].requestId,
            url: msg.requests[i].url,
            bearer: msg.requests[i].bearer,
            body: msg.requests[i].body
          }, "*");
        }
      }
    } catch (e) {
      console.log("[Imperio] WS message parse error: " + e.message);
    }
  };
  _ws.onclose = function() {
    _wsConnected = false;
    _ws = null;
    if (!_wsReconnectTimer) {
      _wsReconnectTimer = setTimeout(function() {
        _wsReconnectTimer = null;
        wsConnect();
      }, 2000);
    }
  };
  _ws.onerror = function() {};
}

function wsSendResult(data) {
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    try {
      _ws.send(JSON.stringify({
        type: "result",
        requestId: data.requestId,
        status: data.status || 0,
        body: data.body || "",
        error: data.error || ""
      }));
      console.log("[Imperio] WS result sent for " + data.requestId);
      activeRequests = Math.max(0, activeRequests - 1);
      return true;
    } catch (e) {}
  }
  return false;
}

window.addEventListener("message", function(event) {
  if (event.source !== window) return;
  if (!event.data) return;

  if (event.data.type === "FLOW_ACCOUNT_HASH") {
    _flowAccountHash = event.data.hash;
    console.log("[Imperio] Flow account hash set: " + _flowAccountHash);
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      _ws.send(JSON.stringify({type: "register", account_hash: _flowAccountHash}));
    }
    return;
  }

  if (event.data.type === "FLOW_GENERATE_RESULT") {
    console.log("[Imperio] Sending result: requestId=" + event.data.requestId + " status=" + (event.data.status || "error"));
    if (wsSendResult(event.data)) return;
    fetch(BRIDGE_BASE + "/flow-generate-result", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        requestId: event.data.requestId,
        status: event.data.status || 0,
        body: event.data.body || "",
        error: event.data.error || ""
      })
    }).then(function(r) {
      console.log("[Imperio] HTTP result sent OK, status=" + r.status);
      activeRequests = Math.max(0, activeRequests - 1);
    }).catch(function(e) {
      console.log("[Imperio] HTTP result error: " + e.message);
      activeRequests = Math.max(0, activeRequests - 1);
    });
  }
});

function pollBridge() {
  if (_wsConnected) return;
  var slotsAvailable = MAX_CONCURRENT - activeRequests;
  if (slotsAvailable <= 0) return;
  if (!_flowAccountHash) return;

  var pollUrl = BRIDGE_BASE + "/flow-generate-poll?account=" + encodeURIComponent(_flowAccountHash);
  fetch(pollUrl)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (pollErrors > 0) {
        console.log("[Imperio] Bridge connection restored");
        pollErrors = 0;
      }
      var requests = [];
      if (data && data.requests && data.requests.length > 0) {
        requests = data.requests;
      } else if (data && data.request) {
        requests = [data.request];
      }
      for (var i = 0; i < requests.length; i++) {
        activeRequests++;
        console.log("[Imperio] HTTP got request: " + requests[i].requestId + " (active=" + activeRequests + ")");
        window.postMessage({
          type: "FLOW_GENERATE_REQUEST",
          requestId: requests[i].requestId,
          url: requests[i].url,
          bearer: requests[i].bearer,
          body: requests[i].body
        }, "*");
      }
    })
    .catch(function(e) {
      pollErrors++;
      if (pollErrors <= 3 || pollErrors % 30 === 0) {
        console.log("[Imperio] Bridge poll error #" + pollErrors + ": " + e.message);
      }
    });
}

wsConnect();
setInterval(pollBridge, POLL_INTERVAL_MS);
setInterval(wsConnect, 5000);
console.log("[Imperio] Flow content script loaded, WS=" + WS_URL + " HTTP=" + BRIDGE_BASE + " (max concurrent=" + MAX_CONCURRENT + ")");

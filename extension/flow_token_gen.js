var SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV";
var RECAPTCHA_ACTION = "IMAGE_GENERATION";
var TOKEN_POOL_SIZE = 5;
var TOKEN_MAX_AGE_MS = 100000;
var _tokenPool = [];
var _tokenFetching = 0;
var _maxTokenFetching = 3;

function _getToken() {
  return new Promise(function(resolve, reject) {
    var now = Date.now();
    while (_tokenPool.length > 0) {
      var entry = _tokenPool.shift();
      if (now - entry.time < TOKEN_MAX_AGE_MS) {
        console.log("[Imperio] Using pre-fetched token (pool=" + _tokenPool.length + ", age=" + Math.round((now - entry.time) / 1000) + "s)");
        _refillPool();
        resolve(entry.token);
        return;
      }
    }
    _refillPool();
    if (typeof grecaptcha === "undefined" || !grecaptcha.enterprise) {
      reject(new Error("grecaptcha not available"));
      return;
    }
    grecaptcha.enterprise.ready(function() {
      grecaptcha.enterprise.execute(SITE_KEY, {action: RECAPTCHA_ACTION})
        .then(function(t) {
          resolve(t);
          _refillPool();
        })
        .catch(reject);
    });
  });
}

function _fetchOneToken() {
  if (typeof grecaptcha === "undefined" || !grecaptcha.enterprise) return;
  if (_tokenFetching >= _maxTokenFetching) return;
  _tokenFetching++;
  grecaptcha.enterprise.ready(function() {
    grecaptcha.enterprise.execute(SITE_KEY, {action: RECAPTCHA_ACTION})
      .then(function(t) {
        _tokenFetching--;
        if (_tokenPool.length < TOKEN_POOL_SIZE) {
          _tokenPool.push({token: t, time: Date.now()});
          console.log("[Imperio] Token pre-fetched (pool=" + _tokenPool.length + ")");
        }
      })
      .catch(function() {
        _tokenFetching--;
      });
  });
}

function _refillPool() {
  var needed = TOKEN_POOL_SIZE - _tokenPool.length - _tokenFetching;
  for (var i = 0; i < needed; i++) {
    _fetchOneToken();
  }
}

function doGenerateRequest(data) {
  if (typeof grecaptcha === "undefined" || !grecaptcha.enterprise) {
    console.log("[Imperio] grecaptcha NOT available");
    window.postMessage({type: "FLOW_GENERATE_RESULT", requestId: data.requestId, error: "grecaptcha not available"}, "*");
    return;
  }

  console.log("[Imperio] doGenerateRequest requestId=" + data.requestId);

  _getToken()
    .then(function(rcToken) {
      console.log("[Imperio] reCAPTCHA token obtained, len=" + rcToken.length);
      var body = JSON.parse(data.body);
      body.clientContext = body.clientContext || {};
      body.clientContext.recaptchaContext = {
        token: rcToken,
        applicationType: "RECAPTCHA_APPLICATION_TYPE_WEB"
      };
      if (body.requests && body.requests.length > 0) {
        body.requests[0].clientContext = body.requests[0].clientContext || {};
        body.requests[0].clientContext.recaptchaContext = {
          token: rcToken,
          applicationType: "RECAPTCHA_APPLICATION_TYPE_WEB"
        };
      }
      return fetch(data.url, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "Authorization": "Bearer " + data.bearer
        },
        body: JSON.stringify(body)
      });
    })
    .then(function(resp) {
      return resp.text().then(function(text) {
        if (resp.status !== 200) {
          console.log("[Imperio] API error status=" + resp.status + " body=" + text.substring(0, 300));
        }
        window.postMessage({
          type: "FLOW_GENERATE_RESULT",
          requestId: data.requestId,
          status: resp.status,
          body: text
        }, "*");
      });
    })
    .catch(function(e) {
      console.log("[Imperio] Fetch error: " + e.toString());
      window.postMessage({
        type: "FLOW_GENERATE_RESULT",
        requestId: data.requestId,
        error: e.toString()
      }, "*");
    });
}

window.addEventListener("message", function(event) {
  if (event.source !== window) return;
  if (!event.data) return;
  if (event.data.type === "FLOW_GENERATE_REQUEST") {
    doGenerateRequest(event.data);
  }
});

var attempts = 0;
var waitInterval = setInterval(function() {
  attempts++;
  if (typeof grecaptcha !== "undefined" && grecaptcha.enterprise) {
    clearInterval(waitInterval);
    console.log("[Imperio] grecaptcha ready, action=" + RECAPTCHA_ACTION);
    _refillPool();
  }
  if (attempts > 120) clearInterval(waitInterval);
}, 500);

function djb2Hash(str) {
  var h = 5381;
  for (var i = 0; i < str.length; i++) {
    h = ((h << 5) + h + str.charCodeAt(i)) & 0xFFFFFFFF;
  }
  var hex = (h >>> 0).toString(16);
  while (hex.length < 8) hex = "0" + hex;
  return hex;
}

fetch("/fx/api/auth/session", {credentials: "include"})
  .then(function(r) { return r.json(); })
  .then(function(data) {
    var user = data.user || {};
    var identity = user.email || user.name || "";
    if (identity) {
      var hash = djb2Hash(identity);
      window.postMessage({type: "FLOW_ACCOUNT_HASH", hash: hash}, "*");
      console.log("[Imperio] Flow account hash: " + hash + " (" + identity + ")");

      // Auto-auth: enviar token al bridge Python (con reintento)
      (function sendAuth(attempt) {
        fetch("http://127.0.0.1:5556/api/auth/auto", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            account_hash: hash,
            access_token: data.access_token,
            email: user.email || "",
            name: user.name || ""
          })
        }).then(function(r) {
          if (r.ok) {
            console.log("[FlowForge] Auto-auth enviado OK (intento " + attempt + ")");
          } else if (attempt < 5) {
            setTimeout(function() { sendAuth(attempt + 1); }, 3000);
          }
        }).catch(function(e) {
          if (attempt < 5) {
            console.log("[FlowForge] Auto-auth retry " + attempt + "/5: " + e.message);
            setTimeout(function() { sendAuth(attempt + 1); }, 3000);
          } else {
            console.log("[FlowForge] Auto-auth failed after 5 intentos. ¿Bridge corriendo?");
          }
        });
      })(1);
    }
  })
  .catch(function(e) {
    console.log("[Imperio] Could not get Flow account: " + e.message);
  });

// Heartbeat: refrescar token cada 60s para mantenerlo vivo
setInterval(function() {
  fetch("/fx/api/auth/session", {credentials: "include"})
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var user = data.user || {};
      var identity = user.email || user.name || "";
      if (identity && data.access_token) {
        var hash = djb2Hash(identity);
        fetch("http://127.0.0.1:5556/api/auth/auto", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            account_hash: hash,
            access_token: data.access_token,
            email: user.email || "",
            name: user.name || ""
          })
        }).catch(function(e) {
          console.log("[FlowForge] Heartbeat failed: " + e.message);
        });
      }
    })
    .catch(function(e) {
      console.log("[FlowForge] Heartbeat session check failed: " + e.message);
    });
}, 60000);

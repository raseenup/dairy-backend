const API_BASE_URL = "https://dairy-backend-9wcg.onrender.com";

async function apiFetch(endpoint, options = {}) {
  const token = localStorage.getItem("access_token");
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {})
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.detail || `HTTP Error ${response.status}`);
  }

  return data;
}

document.addEventListener("DOMContentLoaded", () => {
  checkAuthState();
  loadProducts();
  loadOffers();
});

function checkAuthState() {
  const token = localStorage.getItem("access_token");
  const email = localStorage.getItem("user_email");

  const loginBtn = document.getElementById("login-btn");
  const userProfileNav = document.getElementById("user-profile-nav");
  const userEmailDisplay = document.getElementById("user-email-display");
  const dashboardSection = document.getElementById("dashboard-section");

  if (token && email) {
    loginBtn.classList.add("hidden");
    userProfileNav.classList.remove("hidden");
    userEmailDisplay.textContent = email;
    dashboardSection.classList.remove("hidden");

    loadSubscriptions();
    loadDeliveries();
  } else {
    loginBtn.classList.remove("hidden");
    userProfileNav.classList.add("hidden");
    dashboardSection.classList.add("hidden");
  }
}

function openAuthModal() {
  document.getElementById("auth-modal").classList.remove("hidden");
  resetAuthModal();
}

function closeAuthModal() {
  document.getElementById("auth-modal").classList.add("hidden");
}

function resetAuthModal() {
  document.getElementById("otp-request-step").classList.remove("hidden");
  document.getElementById("otp-verify-step").classList.add("hidden");
  document.getElementById("auth-error").classList.add("hidden");
  document.getElementById("auth-email").value = "";
  document.getElementById("auth-otp").value = "";
}

function showAuthError(message) {
  const errBox = document.getElementById("auth-error");
  errBox.textContent = message;
  errBox.classList.remove("hidden");
}

async function handleRequestOTP() {
  const email = document.getElementById("auth-email").value.trim();
  const sendBtn = document.getElementById("send-otp-btn");

  if (!email) return showAuthError("Please enter a valid email address.");

  try {
    sendBtn.disabled = true;
    sendBtn.textContent = "Sending code...";

    await apiFetch("/auth/request-otp", {
      method: "POST",
      body: JSON.stringify({ email })
    });

    localStorage.setItem("pending_email", email);
    document.getElementById("target-email-display").textContent = email;
    document.getElementById("otp-request-step").classList.add("hidden");
    document.getElementById("otp-verify-step").classList.remove("hidden");
  } catch (err) {
    showAuthError(err.message);
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = "Send Verification Code";
  }
}

async function handleVerifyOTP() {
  const email = localStorage.getItem("pending_email");
  const otp = document.getElementById("auth-otp").value.trim();
  const verifyBtn = document.getElementById("verify-otp-btn");

  if (!otp || otp.length !== 6) return showAuthError("Please enter a valid 6-digit verification code.");

  try {
    verifyBtn.disabled = true;
    verifyBtn.textContent = "Verifying...";

    const response = await apiFetch("/auth/verify-otp", {
      method: "POST",
      body: JSON.stringify({ email, otp })
    });

    localStorage.setItem("access_token", response.access_token);
    localStorage.setItem("user_email", email);
    localStorage.removeItem("pending_email");

    closeAuthModal();
    checkAuthState();
  } catch (err) {
    showAuthError(err.message);
  } finally {
    verifyBtn.disabled = false;
    verifyBtn.textContent = "Verify & Log In";
  }
}

function logout() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("user_email");
  checkAuthState();
}

async function loadProducts() {
  const container = document.getElementById("product-grid");
  try {
    const products = await apiFetch("/products");
    if (!products.length) {
      container.innerHTML = `<p class="col-span-full text-center text-gray-500">No products available.</p>`;
      return;
    }

    container.innerHTML = products.map(p => `
      <div class="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col justify-between p-5 hover:shadow-md transition-shadow">
        <div>
          <div class="flex justify-between items-start mb-2">
            <h4 class="font-bold text-lg text-gray-900">${p.name}</h4>
            <span class="text-xs font-semibold px-2 py-0.5 rounded bg-emerald-50 text-emerald-700">${p.unit}</span>
          </div>
          <p class="text-sm text-gray-600 mb-4">${p.description || 'Fresh, unadulterated farm dairy.'}</p>
        </div>
        <div class="flex items-center justify-between border-t border-gray-100 pt-4">
          <span class="text-xl font-extrabold text-gray-900">₹${p.price}</span>
          <button onclick="handleSubscribe('${p.id}')" class="bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold px-3 py-2 rounded-lg transition-colors">
            Subscribe Now
          </button>
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="col-span-full text-center text-red-500 text-sm">Failed to load product catalog.</p>`;
  }
}

async function loadOffers() {
  try {
    const offers = await apiFetch("/offers?tier=all");
    const banner = document.getElementById("offers-banner");
    if (offers && offers.length > 0) {
      const topOffer = offers[0];
      banner.textContent = `🎉 Special Offer: ${topOffer.title} - ${topOffer.description}`;
      banner.classList.remove("hidden");
    }
  } catch (err) {
    console.error("Offers failed to load silently.");
  }
}

async function loadSubscriptions() {
  const container = document.getElementById("subscriptions-container");
  try {
    const subs = await apiFetch("/subscriptions");
    if (!subs.length) {
      container.innerHTML = `<p class="text-gray-500 text-sm">No active subscriptions found. Pick a product above to subscribe!</p>`;
      return;
    }
    container.innerHTML = subs.map(s => `
      <div class="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
        <div>
          <span class="font-bold text-gray-900 block text-sm">Product ID: ${s.product_id}</span>
          <span class="text-xs text-gray-500">Qty: ${s.quantity} | Frequency: ${s.frequency}</span>
          ${s.pause_until ? `<span class="block text-xs text-amber-600 font-semibold">Paused until ${s.pause_until}</span>` : ''}
        </div>
        <div class="flex items-center space-x-2">
          <span class="text-xs font-bold px-2.5 py-1 rounded-full bg-emerald-100 text-emerald-800 uppercase">${s.status}</span>
          <button onclick="pauseSub('${s.id}')" class="text-xs text-amber-700 border border-amber-300 px-2 py-1 rounded hover:bg-amber-50">Pause</button>
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="text-red-500 text-sm">Failed to load subscriptions.</p>`;
  }
}

async function pauseSub(subId) {
  const date = prompt("Pause dispatches until date (YYYY-MM-DD):", "2026-11-20");
  if(!date) return;
  try {
    await apiFetch(`/subscriptions/${subId}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "paused", pause_until: date })
    });
    alert("Subscription paused successfully!");
    loadSubscriptions();
  } catch(err) {
    alert("Failed to pause subscription: " + err.message);
  }
}

async function loadDeliveries() {
  const container = document.getElementById("deliveries-container");
  try {
    const deliveries = await apiFetch("/deliveries");
    if (!deliveries.length) {
      container.innerHTML = `<p class="text-gray-500 text-sm">No scheduled deliveries for today.</p>`;
      return;
    }
    container.innerHTML = deliveries.map(d => `
      <div class="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
        <div>
          <span class="font-bold text-gray-900 block text-sm">Date: ${d.delivery_date}</span>
          <span class="text-xs text-gray-500">Quantity: ${d.quantity} unit(s) | Payment: ${d.payment_status || 'Pending'}</span>
        </div>
        <span class="text-xs font-bold px-2.5 py-1 rounded-full bg-blue-100 text-blue-800 uppercase">${d.status}</span>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="text-red-500 text-sm">Failed to load deliveries.</p>`;
  }
}

function handleSubscribe(productId) {
  if (!localStorage.getItem("access_token")) {
    openAuthModal();
  } else {
    const qty = prompt("Enter daily quantity (Units):", "1");
    if(!qty) return;
    apiFetch("/subscriptions", {
      method: "POST",
      body: JSON.stringify({
        product_id: productId,
        quantity: parseInt(qty),
        frequency: "Daily",
        start_date: new Date().toISOString().split('T')[0]
      })
    }).then(() => {
      alert("Subscription activated successfully!");
      loadSubscriptions();
    }).catch(err => alert("Error: " + err.message));
  }
}

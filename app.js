const API_BASE_URL = "https://backend.firstmilkventure.com";
let cart = [];
let allProducts = [];

async function apiFetch(endpoint, options = {}) {
  const token = localStorage.getItem("access_token");
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(`${API_BASE_URL}${endpoint}`, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP Error ${response.status}`);
  return data;
}

document.addEventListener("DOMContentLoaded", () => {
  checkAuthState();
  loadProducts();
  loadSummaryStatuses();
});

function showPage(pageId) {
  ["home", "offers", "search", "subscriptions", "deliveries", "cart", "profile"].forEach(p => {
    const el = document.getElementById(`page-${p}`);
    if (el) el.classList.add("hidden");
  });
  const target = document.getElementById(`page-${pageId}`);
  if (target) target.classList.remove("hidden");

  if (pageId === "profile") renderProfile();
  if (pageId === "subscriptions") loadSubscriptions();
  if (pageId === "deliveries") loadDeliveries();
  if (pageId === "cart") renderCart();
  if (pageId === "offers") renderOffersPage();
  if (pageId === "search") renderSearchPage();
}

function checkAuthState() {
  const token = localStorage.getItem("access_token");
  const email = localStorage.getItem("user_email");
  const loginBtn = document.getElementById("login-btn");
  const userProfileNav = document.getElementById("user-profile-nav");
  if (token && email) {
    if (loginBtn) loginBtn.classList.add("hidden");
    if (userProfileNav) userProfileNav.classList.remove("hidden");
    document.getElementById("user-name-display").textContent = localStorage.getItem("user_name") || email;
  } else {
    if (loginBtn) loginBtn.classList.remove("hidden");
    if (userProfileNav) userProfileNav.classList.add("hidden");
  }
}

function openAuthModal() {
  document.getElementById("auth-modal").classList.remove("hidden");
  hideAuthError();
}

function closeAuthModal() {
  document.getElementById("auth-modal").classList.add("hidden");
}

function showAuthError(msg) {
  const box = document.getElementById("auth-error");
  box.textContent = msg;
  box.classList.remove("hidden");
}

function hideAuthError() {
  const box = document.getElementById("auth-error");
  box.classList.add("hidden");
}

function setAuthTab(tab) {
  hideAuthError();
  const signinBtn = document.getElementById("tab-signin-btn");
  const signupBtn = document.getElementById("tab-signup-btn");
  const signinSec = document.getElementById("signin-section");
  const signupSec = document.getElementById("signup-section");
  if (tab === 'signin') {
    signinBtn.className = "py-2.5 text-xs font-bold rounded-xl border border-emerald-600 bg-emerald-50 text-emerald-700";
    signupBtn.className = "py-2.5 text-xs font-bold rounded-xl border border-gray-200 bg-gray-50 text-gray-600";
    signinSec.classList.remove("hidden");
    signupSec.classList.add("hidden");
  } else {
    signupBtn.className = "py-2.5 text-xs font-bold rounded-xl border border-emerald-600 bg-emerald-50 text-emerald-700";
    signinBtn.className = "py-2.5 text-xs font-bold rounded-xl border border-gray-200 bg-gray-50 text-gray-600";
    signupSec.classList.remove("hidden");
    signinSec.classList.add("hidden");
  }
}

async function handleSignin() {
  hideAuthError();
  const email_or_phone = document.getElementById("signin-identifier").value.trim();
  const password = document.getElementById("signin-password").value.trim();
  if (!email_or_phone) return showAuthError("Please enter your email or phone number.");

  try {
    const res = await apiFetch("/auth/signin", {
      method: "POST",
      body: JSON.stringify({ email_or_phone, password })
    });
    localStorage.setItem("access_token", res.access_token);
    localStorage.setItem("user_email", res.user.email);
    localStorage.setItem("user_phone", res.user.phone || "");
    localStorage.setItem("user_name", res.user.full_name || "");
    localStorage.setItem("user_address", res.user.address || "");
    closeAuthModal();
    checkAuthState();
    loadSummaryStatuses();
    showPage("home");
  } catch (err) {
    showAuthError("Sign in failed. " + err.message);
  }
}

async function handleSignupRequestOTP() {
  hideAuthError();
  const email = document.getElementById("signup-email").value.trim();
  const phone = document.getElementById("signup-phone").value.trim();
  const password = document.getElementById("signup-password").value.trim();

  if (!email || !phone || !password) {
    return showAuthError("Please fill in email, phone number, and custom password.");
  }

  try {
    await apiFetch("/auth/request-otp", {
      method: "POST",
      body: JSON.stringify({ email, phone, password })
    });
    localStorage.setItem("pending_signup_email", email);
    localStorage.setItem("pending_signup_phone", phone);
    localStorage.setItem("pending_signup_password", password);

    document.getElementById("signup-step-1").classList.add("hidden");
    document.getElementById("signup-step-2").classList.remove("hidden");
  } catch (err) {
    showAuthError(err.message);
  }
}

async function handleSignupVerifyOTP() {
  hideAuthError();
  const email = localStorage.getItem("pending_signup_email");
  const phone = localStorage.getItem("pending_signup_phone");
  const password = localStorage.getItem("pending_signup_password");
  const otp = document.getElementById("signup-otp").value.trim();

  if (!otp || otp.length !== 6) return showAuthError("Please enter valid 6-digit OTP.");

  try {
    const res = await apiFetch("/auth/verify-otp", {
      method: "POST",
      body: JSON.stringify({ email, phone, password, otp })
    });
    localStorage.setItem("access_token", res.access_token);
    localStorage.setItem("user_email", email);
    localStorage.setItem("user_phone", phone);

    closeAuthModal();
    checkAuthState();
    loadSummaryStatuses();
    showPage("profile");
    alert("Signup complete! Please update your full name & delivery address in Profile.");
  } catch (err) {
    showAuthError(err.message);
  }
}

async function loadProducts() {
  try {
    allProducts = await apiFetch("/products");
    renderProductCards(allProducts, "product-grid");
  } catch (err) {}
}

function renderProductCards(products, containerId) {
  const grid = document.getElementById(containerId);
  if (!grid) return;
  grid.innerHTML = products.map(p => `
    <div class="bg-white rounded-2xl shadow-sm border border-gray-200 p-5 flex flex-col justify-between">
      <div>
        <h4 class="font-bold text-gray-900 text-base mb-1">${p.name}</h4>
        <p class="text-xs text-gray-500 mb-3">${p.description || ''}</p>
        <div class="text-lg font-black text-emerald-700 mb-4">₹${p.price} / ${p.unit || 'Liter'}</div>
      </div>
      <div class="space-y-2">
        <div class="flex items-center space-x-2">
          <input type="number" id="qty-${p.id}" value="1" min="1" max="50" class="w-16 text-center text-xs border border-gray-300 rounded-xl py-2 font-bold focus:outline-none focus:ring-2 focus:ring-emerald-500" />
          <button onclick="addMultiToCart('${p.id}', '${p.name}', ${p.price})" class="flex-1 bg-gray-100 hover:bg-emerald-50 text-gray-800 hover:text-emerald-800 text-xs font-bold py-2 rounded-xl border border-gray-200 transition-colors">
            🛒 Add Qty in 1 Click
          </button>
        </div>
        <button onclick="handleSubscribe('${p.id}', '${p.name}')" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold py-2 rounded-xl transition-colors">
          📅 Subscribe Plan
        </button>
      </div>
    </div>
  `).join("");
}

function addMultiToCart(id, name, price) {
  const qtyInput = document.getElementById(`qty-${id}`);
  const qty = parseInt(qtyInput ? qtyInput.value : 1) || 1;
  const existing = cart.find(i => i.id === id);
  if (existing) {
    existing.qty += qty;
  } else {
    cart.push({ id, name, price, qty });
  }
  updateCartBadge();
  alert(`Added ${qty} unit(s) of ${name} to cart!`);
}

function updateCartBadge() {
  const total = cart.reduce((acc, item) => acc + item.qty, 0);
  const badgeDesktop = document.getElementById("cart-badge-desktop");
  if (badgeDesktop) {
    if (total > 0) {
      badgeDesktop.textContent = total;
      badgeDesktop.classList.remove("hidden");
    } else {
      badgeDesktop.classList.add("hidden");
    }
  }
}

async function loadSummaryStatuses() {
  try {
    const deliveries = await apiFetch("/deliveries");
    const subs = await apiFetch("/subscriptions");
    
    if (deliveries && deliveries.length > 0) {
      const latest = deliveries[0];
      document.getElementById("home-order-status-summary").textContent = `Order #${latest.id.slice(0,6)}: ${latest.status.toUpperCase()}`;
      document.getElementById("home-order-detail").textContent = `Date: ${latest.delivery_date} | Qty: ${latest.quantity}`;
    }
    
    if (subs && subs.length > 0) {
      const activeSubs = subs.filter(s => s.status === 'active');
      document.getElementById("home-sub-status-summary").textContent = `${activeSubs.length} Active Subscription(s)`;
      document.getElementById("home-sub-detail").textContent = `Product: ${activeSubs[0]?.product_name || 'Milk Plan'}`;
    }
  } catch (e) {}
}

async function renderProfile() {
  const container = document.getElementById("profile-card");
  const email = localStorage.getItem("user_email");
  if (!email) {
    container.innerHTML = `<p class="text-xs text-gray-500">Please sign in to manage profile details.</p>`;
    return;
  }

  try {
    const profile = await apiFetch("/users/me");
    container.innerHTML = `
      <div class="space-y-4 text-xs">
        <div>
          <label class="block font-bold text-gray-700 mb-1">Registered Email ID (Read-only)</label>
          <input type="text" value="${profile.email || email}" disabled class="w-full px-3 py-2 border rounded-xl bg-gray-100 text-gray-500 font-semibold" />
        </div>
        <div>
          <label class="block font-bold text-gray-700 mb-1">Full Name *</label>
          <input type="text" id="profile-name" value="${profile.full_name || localStorage.getItem("user_name") || ""}" placeholder="Enter full name" class="w-full px-3 py-2 border rounded-xl font-semibold" />
        </div>
        <div>
          <label class="block font-bold text-gray-700 mb-1">Mobile Phone Number *</label>
          <input type="text" id="profile-phone" value="${profile.phone || localStorage.getItem("user_phone") || ""}" placeholder="Enter mobile number" class="w-full px-3 py-2 border rounded-xl font-semibold" />
        </div>
        <div>
          <label class="block font-bold text-gray-700 mb-1">Full Delivery Address & Zone Details *</label>
          <textarea id="profile-address" rows="3" placeholder="Street, house number, area zone..." class="w-full px-3 py-2 border rounded-xl font-semibold">${profile.address || localStorage.getItem("user_address") || ""}</textarea>
        </div>
      </div>
    `;
  } catch (err) {
    container.innerHTML = `<p class="text-xs text-red-500">Failed to load profile details.</p>`;
  }
}

async function saveProfileChanges() {
  const full_name = document.getElementById("profile-name").value.trim();
  const phone = document.getElementById("profile-phone").value.trim();
  const address = document.getElementById("profile-address").value.trim();

  try {
    await apiFetch("/users/me", {
      method: "PATCH",
      body: JSON.stringify({ full_name, phone, address })
    });
    localStorage.setItem("user_name", full_name);
    localStorage.setItem("user_phone", phone);
    localStorage.setItem("user_address", address);
    checkAuthState();
    alert("Profile details updated successfully!");
  } catch (err) {
    alert("Failed to save profile: " + err.message);
  }
}

function logout() {
  localStorage.clear();
  checkAuthState();
  showPage("home");
}

const API_BASE_URL = "https://dairy-backend-9wcg.onrender.com";
let currentRole = "customer";
let cart = [];
let allProducts = [];

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

function showPage(pageId) {
  const pages = ["home", "subscriptions", "deliveries", "cart", "profile"];
  pages.forEach(p => {
    const el = document.getElementById(`page-${p}`);
    if (el) el.classList.add("hidden");
  });
  const target = document.getElementById(`page-${pageId}`);
  if (target) target.classList.remove("hidden");

  if (pageId === "subscriptions") loadSubscriptions();
  if (pageId === "deliveries") loadDeliveries();
  if (pageId === "cart") renderCart();
  if (pageId === "profile") renderProfile();
}

function checkAuthState() {
  const token = localStorage.getItem("access_token");
  const email = localStorage.getItem("user_email");
  const name = localStorage.getItem("user_name") || email;

  const loginBtn = document.getElementById("login-btn");
  const userProfileNav = document.getElementById("user-profile-nav");
  const userNameDisplay = document.getElementById("user-name-display");

  if (token && email) {
    if (loginBtn) loginBtn.classList.add("hidden");
    if (userProfileNav) userProfileNav.classList.remove("hidden");
    if (userNameDisplay) userNameDisplay.textContent = name;
  } else {
    if (loginBtn) loginBtn.classList.remove("hidden");
    if (userProfileNav) userProfileNav.classList.add("hidden");
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
  document.getElementById("customer-auth-form").classList.remove("hidden");
  document.getElementById("otp-verify-step").classList.add("hidden");
  document.getElementById("staff-auth-form").classList.add("hidden");
  document.getElementById("auth-error").classList.add("hidden");
}

function setAuthRole(role) {
  currentRole = role;
  ["customer", "driver", "admin"].forEach(r => {
    const btn = document.getElementById(`role-btn-${r}`);
    if (btn) {
      if (r === role) {
        btn.className = "py-2 text-xs font-bold rounded-lg border border-emerald-600 bg-emerald-50 text-emerald-700";
      } else {
        btn.className = "py-2 text-xs font-bold rounded-lg border border-gray-200 bg-gray-50 text-gray-600";
      }
    }
  });

  if (role === "customer") {
    document.getElementById("customer-auth-form").classList.remove("hidden");
    document.getElementById("staff-auth-form").classList.add("hidden");
  } else {
    document.getElementById("customer-auth-form").classList.add("hidden");
    document.getElementById("otp-verify-step").classList.add("hidden");
    document.getElementById("staff-auth-form").classList.remove("hidden");
    document.getElementById("staff-title").textContent = role === "driver" ? "Delivery Driver Portal Login" : "Category Admin Portal Login";
    document.getElementById("staff-key").value = role === "driver" ? "DriverMilk2026" : "Firstmilk3211234";
  }
}

function showAuthError(msg) {
  const box = document.getElementById("auth-error");
  box.textContent = msg;
  box.classList.remove("hidden");
}

async function handleRequestOTP() {
  const email = document.getElementById("auth-email").value.trim();
  const phone = document.getElementById("auth-phone").value.trim();
  const name = document.getElementById("auth-name").value.trim();
  const address = document.getElementById("auth-address").value.trim();

  if (!email || !phone) return showAuthError("Please provide both email and mobile number.");

  try {
    await apiFetch("/auth/request-otp", {
      method: "POST",
      body: JSON.stringify({ email, phone, full_name: name, address })
    });

    localStorage.setItem("pending_email", email);
    localStorage.setItem("pending_phone", phone);
    localStorage.setItem("pending_name", name || email.split("@")[0]);
    localStorage.setItem("pending_address", address);

    document.getElementById("target-email-display").textContent = email;
    document.getElementById("customer-auth-form").classList.add("hidden");
    document.getElementById("otp-verify-step").classList.remove("hidden");
  } catch (err) {
    showAuthError(err.message);
  }
}

async function handleVerifyOTP() {
  const email = localStorage.getItem("pending_email");
  const phone = localStorage.getItem("pending_phone");
  const name = localStorage.getItem("pending_name");
  const address = localStorage.getItem("pending_address");
  const otp = document.getElementById("auth-otp").value.trim();

  if (!otp || otp.length !== 6) return showAuthError("Enter valid 6-digit OTP.");

  try {
    const res = await apiFetch("/auth/verify-otp", {
      method: "POST",
      body: JSON.stringify({ email, phone, otp, full_name: name, address })
    });

    localStorage.setItem("access_token", res.access_token);
    localStorage.setItem("user_email", email);
    localStorage.setItem("user_phone", phone);
    localStorage.setItem("user_name", name);
    localStorage.setItem("user_address", address);

    closeAuthModal();
    checkAuthState();
    showPage("home");
  } catch (err) {
    showAuthError(err.message);
  }
}

function handleStaffLogin() {
  window.location.href = "admin.html";
}

function logout() {
  localStorage.clear();
  checkAuthState();
  showPage("home");
}

async function loadProducts() {
  const grid = document.getElementById("product-grid");
  try {
    allProducts = await apiFetch("/products");
    renderProductCards(allProducts);
  } catch (err) {
    grid.innerHTML = `<p class="col-span-full text-center text-red-500 text-xs">Failed to load catalog.</p>`;
  }
}

function renderProductCards(products) {
  const grid = document.getElementById("product-grid");
  if (!products.length) {
    grid.innerHTML = `<p class="col-span-full text-center text-gray-500 text-xs">No matching products found.</p>`;
    return;
  }

  grid.innerHTML = products.map(p => `
    <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-5 flex flex-col justify-between hover:shadow-md transition-shadow">
      <div>
        <div class="flex justify-between items-start mb-2">
          <h4 class="font-bold text-gray-900 text-base">${p.name}</h4>
          <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-100 text-emerald-800">${p.unit || 'Liter'}</span>
        </div>
        <p class="text-xs text-gray-500 mb-4 leading-relaxed">${p.description || 'Pure organic farm fresh dairy product.'}</p>
      </div>
      <div>
        <div class="text-lg font-black text-emerald-700 mb-3">₹${p.price}</div>
        <div class="grid grid-cols-2 gap-2">
          <button onclick="addToCart('${p.id}', '${p.name}', ${p.price})" class="bg-gray-100 hover:bg-gray-200 text-gray-800 text-xs font-bold py-2 rounded-lg transition-colors">
            🛒 Add Single Item
          </button>
          <button onclick="handleSubscribe('${p.id}', '${p.name}')" class="bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold py-2 rounded-lg transition-colors">
            📅 Subscribe
          </button>
        </div>
      </div>
    </div>
  `).join("");
}

function filterProducts() {
  const query = document.getElementById("top-search-bar").value.toLowerCase();
  const filtered = allProducts.filter(p => p.name.toLowerCase().includes(query) || (p.description && p.description.toLowerCase().includes(query)));
  renderProductCards(filtered);
}

function addToCart(id, name, price) {
  const existing = cart.find(i => i.id === id);
  if (existing) {
    existing.qty += 1;
  } else {
    cart.push({ id, name, price, qty: 1 });
  }
  updateCartBadge();
  alert(`Added ${name} to cart!`);
}

function updateCartBadge() {
  const total = cart.reduce((acc, item) => acc + item.qty, 0);
  const badge = document.getElementById("cart-badge");
  if (total > 0) {
    badge.textContent = total;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

function renderCart() {
  const container = document.getElementById("cart-items-container");
  if (!cart.length) {
    container.innerHTML = `<p class="text-gray-500 text-xs">Your cart is empty. Pick a product from the home catalog!</p>`;
    return;
  }

  let total = 0;
  let itemsHtml = cart.map((item, index) => {
    const itemTotal = item.price * item.qty;
    total += itemTotal;
    return `
      <div class="flex items-center justify-between py-2 border-b last:border-0">
        <div>
          <span class="font-bold text-xs text-gray-800">${item.name}</span>
          <span class="text-[11px] text-gray-500 block">₹${item.price} x ${item.qty}</span>
        </div>
        <div class="flex items-center space-x-3">
          <span class="font-bold text-xs text-emerald-700">₹${itemTotal}</span>
          <button onclick="removeFromCart(${index})" class="text-red-500 font-bold text-xs">✕</button>
        </div>
      </div>
    `;
  }).join("");

  container.innerHTML = `
    <div class="space-y-4">
      ${itemsHtml}
      <div class="flex justify-between items-center border-t pt-3">
        <span class="font-bold text-sm">Total Amount:</span>
        <span class="text-lg font-black text-emerald-700">₹${total}</span>
      </div>
      <button onclick="checkoutCart(${total})" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-2.5 rounded-lg text-xs shadow">
        Proceed to Pay via Moyasar / Stripe SDK →
      </button>
    </div>
  `;
}

function removeFromCart(index) {
  cart.splice(index, 1);
  updateCartBadge();
  renderCart();
}

async function checkoutCart(amount) {
  if (!localStorage.getItem("access_token")) return openAuthModal();

  try {
    const res = await apiFetch("/webhooks/payment", {
      method: "POST",
      body: JSON.stringify({
        event: "payment.succeeded",
        order_id: "ORD-" + Math.floor(Math.random()*100000),
        transaction_id: "SDK-MOYASAR-" + Math.floor(Math.random()*1000000),
        amount: amount,
        status: "paid"
      })
    });

    alert(`✅ Payment of ₹${amount} successful!\nTransaction ID: ${res.transaction_id || 'TXN-9988'}`);
    cart = [];
    updateCartBadge();
    showPage("deliveries");
  } catch (err) {
    alert("Checkout failed: " + err.message);
  }
}

async function loadOffers() {
  try {
    const offers = await apiFetch("/offers?tier=all");
    const banner = document.getElementById("offers-banner");
    if (offers && offers.length > 0) {
      const topOffer = offers[0];
      banner.textContent = `🎉 Promo Offer: ${topOffer.title} - ${topOffer.description}`;
      banner.classList.remove("hidden");
    }
  } catch (err) {}
}

async function loadSubscriptions() {
  const container = document.getElementById("subscriptions-container");
  try {
    const subs = await apiFetch("/subscriptions");
    if (!subs.length) {
      container.innerHTML = `<p class="text-gray-500 text-xs">No active subscriptions found. Select a product to subscribe!</p>`;
      return;
    }

    container.innerHTML = subs.map(s => `
      <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-4 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <div class="flex items-center space-x-2">
            <span class="font-bold text-emerald-800 text-sm">Subscription #${s.subscription_number || s.id}</span>
            <span class="text-[10px] uppercase font-bold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800">${s.status}</span>
          </div>
          <p class="text-xs font-semibold text-gray-700 mt-1">Product: <span class="text-emerald-700 font-bold">${s.product_name || s.product_id}</span></p>
          <p class="text-[11px] text-gray-500">Daily Quantity: ${s.quantity} Unit(s) | Frequency: ${s.frequency}</p>
          ${s.pause_until ? `<p class="text-[11px] text-amber-600 font-bold mt-1">⏸ Paused dispatches until: ${s.pause_until}</p>` : ''}
        </div>
        <div class="flex space-x-2">
          <button onclick="pauseSub('${s.id}')" class="text-xs text-amber-700 border border-amber-300 px-3 py-1.5 rounded-lg hover:bg-amber-50 font-semibold">Pause Plan</button>
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="text-red-500 text-xs">Failed to load subscriptions.</p>`;
  }
}

async function pauseSub(subId) {
  const date = prompt("Pause daily dispatches until date (YYYY-MM-DD):", "2026-11-20");
  if (!date) return;
  try {
    await apiFetch(`/subscriptions/${subId}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "paused", pause_until: date })
    });
    alert("Subscription paused successfully!");
    loadSubscriptions();
  } catch (err) {
    alert("Failed: " + err.message);
  }
}

async function loadDeliveries() {
  const container = document.getElementById("deliveries-container");
  try {
    const deliveries = await apiFetch("/deliveries");
    if (!deliveries.length) {
      container.innerHTML = `<p class="text-gray-500 text-xs">No delivery orders scheduled.</p>`;
      return;
    }

    container.innerHTML = deliveries.map(d => `
      <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-4 flex justify-between items-center">
        <div>
          <div class="flex items-center space-x-2">
            <span class="font-bold text-gray-900 text-xs">Order #${d.order_number || d.id}</span>
            <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 uppercase">${d.status}</span>
          </div>
          <p class="text-xs font-semibold text-gray-700 mt-1">Product: <span class="text-emerald-700 font-bold">${d.product_name || d.product_id}</span></p>
          <p class="text-[11px] text-gray-500">Delivery Date: ${d.delivery_date} | Qty: ${d.quantity} unit(s) | Payment: ${d.payment_status || 'Paid'}</p>
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="text-red-500 text-xs">Failed to load delivery schedule.</p>`;
  }
}

function handleSubscribe(productId, productName) {
  if (!localStorage.getItem("access_token")) return openAuthModal();

  const qty = prompt(`Subscribe to ${productName}.\nEnter daily quantity (Units):`, "1");
  if (!qty) return;

  apiFetch("/subscriptions", {
    method: "POST",
    body: JSON.stringify({
      product_id: productId,
      product_name: productName,
      quantity: parseInt(qty),
      frequency: "Daily",
      start_date: new Date().toISOString().split('T')[0]
    })
  }).then(() => {
    alert("Subscription activated successfully!");
    showPage("subscriptions");
  }).catch(err => alert("Error: " + err.message));
}

function renderProfile() {
  const container = document.getElementById("profile-card");
  const email = localStorage.getItem("user_email");
  const phone = localStorage.getItem("user_phone") || "+966 50 123 4567";
  const name = localStorage.getItem("user_name") || "Muhammed Raseen";
  const address = localStorage.getItem("user_address") || "Hail Street, Ruwais District, Jeddah, Saudi Arabia";

  if (!email) {
    container.innerHTML = `<p class="text-gray-500 text-xs">Please sign in to view profile details.</p>`;
    return;
  }

  container.innerHTML = `
    <div class="space-y-3">
      <div class="flex items-center space-x-3 pb-3 border-b">
        <div class="w-12 h-12 bg-emerald-100 text-emerald-800 font-bold rounded-full flex items-center justify-center text-lg">
          ${name.charAt(0)}
        </div>
        <div>
          <h3 class="font-bold text-gray-900 text-base">${name}</h3>
          <span class="text-xs text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded font-semibold">Verified Customer</span>
        </div>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
        <div>
          <span class="text-gray-500 block font-medium">Email Address</span>
          <span class="font-bold text-gray-800">${email}</span>
        </div>
        <div>
          <span class="text-gray-500 block font-medium">Mobile Number</span>
          <span class="font-bold text-gray-800">${phone}</span>
        </div>
        <div class="col-span-full">
          <span class="text-gray-500 block font-medium">Default Delivery Address</span>
          <span class="font-bold text-gray-800">${address}</span>
        </div>
      </div>
    </div>
  `;
}

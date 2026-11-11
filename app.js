const API_BASE_URL = "https://backend.firstmilkventure.com";
let cart = [];
let productsCatalog = [
  { id: "prod-1", name: "Pure Cow Ghee", description: "Pure organic farm fresh dairy product.", price: 350.0, unit: "500g", stock_quantity: 100 },
  { id: "prod-2", name: "Fresh Curd", description: "Pure organic farm fresh dairy product.", price: 40.0, unit: "500g", stock_quantity: 120 },
  { id: "prod-3", name: "Unsalted Butter", description: "Pure organic farm fresh dairy product.", price: 120.0, unit: "250g", stock_quantity: 80 }
];

document.addEventListener("DOMContentLoaded", () => {
  loadProducts();
  checkUserSession();
});

function loadProducts() {
  fetch(`${API_BASE_URL}/products`)
    .then(res => res.json())
    .then(data => {
      if (Array.isArray(data) && data.length > 0) {
        productsCatalog = data;
      }
      renderProducts();
    })
    .catch(() => {
      renderProducts();
    });
}

function renderProducts() {
  const grid = document.getElementById("product-grid");
  grid.innerHTML = productsCatalog.map(p => `
    <div class="bg-white rounded-3xl p-5 border border-gray-200 shadow-sm flex flex-col justify-between space-y-4">
      <div>
        <div class="flex justify-between items-start">
          <h4 class="font-black text-gray-900 text-sm">${p.name}</h4>
          <span class="bg-emerald-50 text-emerald-700 text-[10px] font-bold px-2.5 py-1 rounded-full">${p.unit || 'Liter'}</span>
        </div>
        <p class="text-xs text-gray-500 mt-1">${p.description || 'Pure organic farm fresh dairy.'}</p>
      </div>
      
      <div class="flex items-center justify-between pt-2 border-t border-gray-100">
        <span class="font-black text-emerald-700 text-base">₹${p.price}</span>
        
        <!-- Multi-Quantity Selector & Add Buttons -->
        <div class="flex items-center space-x-2">
          <div class="flex items-center border border-gray-200 rounded-xl overflow-hidden bg-gray-50">
            <button onclick="adjustQty('${p.id}', -1)" class="px-2 py-1 text-xs font-bold text-gray-600 hover:bg-gray-200">-</button>
            <span id="qty-${p.id}" class="px-2.5 text-xs font-black text-gray-900">1</span>
            <button onclick="adjustQty('${p.id}', 1)" class="px-2 py-1 text-xs font-bold text-gray-600 hover:bg-gray-200">+</button>
          </div>
          <button onclick="addToCart('${p.id}')" class="bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs px-3 py-2 rounded-xl shadow transition-colors">
            Add to Cart
          </button>
        </div>
      </div>
    </div>
  `).join("");
}

function adjustQty(productId, delta) {
  const el = document.getElementById(`qty-${productId}`);
  let current = parseInt(el.textContent) || 1;
  current = Math.max(1, current + delta);
  el.textContent = current;
}

function addToCart(productId) {
  const prod = productsCatalog.find(p => p.id === productId);
  const qty = parseInt(document.getElementById(`qty-${productId}`).textContent) || 1;
  
  if (!prod) return;

  const existing = cart.find(item => item.id === productId);
  if (existing) {
    existing.quantity += qty;
  } else {
    cart.push({ ...prod, quantity: qty });
  }

  updateCartBadge();
  alert(`✅ Added ${qty} x ${prod.name} to cart!`);
}

function updateCartBadge() {
  const badge = document.getElementById("cart-badge");
  const totalCount = cart.reduce((sum, item) => sum + item.quantity, 0);
  if (totalCount > 0) {
    badge.textContent = totalCount;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

function openCartDrawer() {
  document.getElementById("cart-drawer").classList.remove("hidden");
  renderCartItems();
}

function closeCartDrawer() {
  document.getElementById("cart-drawer").classList.add("hidden");
}

function renderCartItems() {
  const container = document.getElementById("cart-items-container");
  const totalPriceEl = document.getElementById("cart-total-price");

  if (!cart.length) {
    container.innerHTML = `<p class="text-xs text-gray-400 italic text-center py-8">Your cart is empty.</p>`;
    totalPriceEl.textContent = "₹0.00";
    return;
  }

  let total = 0;
  container.innerHTML = cart.map((item, index) => {
    total += item.price * item.quantity;
    return `
      <div class="flex justify-between items-center bg-gray-50 p-3 rounded-2xl border">
        <div>
          <h5 class="font-bold text-xs text-gray-900">${item.name}</h5>
          <span class="text-[10px] text-gray-500">₹${item.price} x ${item.quantity} = ₹${item.price * item.quantity}</span>
        </div>
        <div class="flex items-center space-x-2">
          <button onclick="removeFromCart(${index})" class="text-red-500 font-bold text-xs px-2 py-1 bg-red-50 rounded-lg">Remove</button>
        </div>
      </div>
    `;
  }).join("");

  totalPriceEl.textContent = `₹${total.toFixed(2)}`;
}

function removeFromCart(index) {
  cart.splice(index, 1);
  renderCartItems();
  updateCartBadge();
}

function checkoutCart() {
  const token = localStorage.getItem("customer_token");
  if (!token) {
    alert("Please sign in or register to complete your order.");
    closeCartDrawer();
    openAuthModal('signin');
    return;
  }
  alert("🎉 Order placed successfully! Our delivery partner will deliver your fresh dairy items tomorrow morning.");
  cart = [];
  updateCartBadge();
  closeCartDrawer();
}

// Auth modal handlers (Sign In / Sign Up)
function openAuthModal(mode = 'signin') {
  document.getElementById("auth-modal").classList.remove("hidden");
  setAuthTab(mode);
}

function closeAuthModal() {
  document.getElementById("auth-modal").classList.add("hidden");
}

function setAuthTab(tab) {
  const btnSignin = document.getElementById("tab-signin");
  const btnSignup = document.getElementById("tab-signup");
  const formSignin = document.getElementById("form-signin");
  const formSignup = document.getElementById("form-signup");

  if (tab === 'signin') {
    btnSignin.className = "flex-1 py-2 rounded-xl bg-white text-gray-900 shadow-sm transition-all font-bold";
    btnSignup.className = "flex-1 py-2 rounded-xl text-gray-500 transition-all";
    formSignin.classList.remove("hidden");
    formSignup.classList.add("hidden");
  } else {
    btnSignup.className = "flex-1 py-2 rounded-xl bg-white text-gray-900 shadow-sm transition-all font-bold";
    btnSignin.className = "flex-1 py-2 rounded-xl text-gray-500 transition-all";
    formSignup.classList.remove("hidden");
    formSignin.classList.add("hidden");
  }
}

async function handleCustomerSignIn() {
  const identifier = document.getElementById("signin-identifier").value.trim();
  const password = document.getElementById("signin-password").value.trim();
  const errBox = document.getElementById("signin-error");
  errBox.classList.add("hidden");

  if (!identifier || !password) {
    errBox.textContent = "Please enter email/phone and password.";
    errBox.classList.remove("hidden");
    return;
  }

  // Simulate or call backend sign in
  localStorage.setItem("customer_token", "sample-jwt-token-2026");
  localStorage.setItem("customer_email", identifier);
  alert("✅ Signed in successfully!");
  closeAuthModal();
  checkUserSession();
}

async function handleCustomerSignUp() {
  const fullName = document.getElementById("signup-fullname").value.trim();
  const email = document.getElementById("signup-email").value.trim();
  const phone = document.getElementById("signup-phone").value.trim();
  const password = document.getElementById("signup-password").value.trim();
  const address = document.getElementById("signup-address").value.trim();
  const errBox = document.getElementById("signup-error");
  errBox.classList.add("hidden");

  if (!email || !phone || !password) {
    errBox.textContent = "Please fill in email, phone number, and password.";
    errBox.classList.remove("hidden");
    return;
  }

  localStorage.setItem("customer_token", "sample-jwt-token-2026");
  localStorage.setItem("customer_email", email);
  localStorage.setItem("customer_name", fullName || email);
  localStorage.setItem("customer_address", address);

  alert("🎉 Account created successfully! You can now manage subscriptions and orders.");
  closeAuthModal();
  checkUserSession();
}

function checkUserSession() {
  const token = localStorage.getItem("customer_token");
  const email = localStorage.getItem("customer_email") || "Customer";
  const name = localStorage.getItem("customer_name") || email.split('@')[0];
  const container = document.getElementById("auth-header-container");

  if (token) {
    container.innerHTML = `
      <div class="flex items-center space-x-2">
        <span class="text-xs font-bold text-emerald-700 bg-emerald-50 px-3 py-1.5 rounded-xl border border-emerald-200">${name}</span>
        <button onclick="handleSignOut()" class="text-xs text-red-600 font-bold hover:underline">Sign Out</button>
      </div>
    `;
    loadDashboardStatus();
  } else {
    container.innerHTML = `
      <button onclick="openAuthModal('signin')" class="bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded-xl text-xs font-bold shadow transition-colors">
        Sign In / Register
      </button>
    `;
  }
}

function handleSignOut() {
  localStorage.removeItem("customer_token");
  localStorage.removeItem("customer_email");
  localStorage.removeItem("customer_name");
  checkUserSession();
  alert("Signed out successfully.");
}

async function loadDashboardStatus() {
  const email = localStorage.getItem("customer_email");
  const subContainer = document.getElementById("dashboard-subscriptions-list");
  const orderContainer = document.getElementById("dashboard-orders-list");

  subContainer.innerHTML = `
    <div class="bg-white p-3 rounded-xl border shadow-sm">
      <span class="font-bold text-gray-900 block">Daily Farm Fresh Milk (1 Liter)</span>
      <span class="text-[10px] text-emerald-700 font-bold uppercase">Status: Active Subscription</span>
    </div>
  `;

  orderContainer.innerHTML = `
    <div class="bg-white p-3 rounded-xl border shadow-sm">
      <span class="font-bold text-gray-900 block">Order #FMV-8821 (Out for Delivery)</span>
      <span class="text-[10px] text-blue-700 font-bold uppercase">Expected Tomorrow Morning (6:00 AM)</span>
    </div>
  `;
}

function openSearchModal() {
  document.getElementById("search-modal").classList.remove("hidden");
  document.getElementById("search-input").focus();
}

function closeSearchModal() {
  document.getElementById("search-modal").classList.add("hidden");
}

function executeSearch() {
  const query = document.getElementById("search-input").value.toLowerCase();
  const container = document.getElementById("search-results-container");

  const filtered = productsCatalog.filter(p => p.name.toLowerCase().includes(query) || p.description.toLowerCase().includes(query));

  if (!filtered.length) {
    container.innerHTML = `<p class="text-xs text-gray-400 italic text-center py-4">No products found matching "${query}".</p>`;
    return;
  }

  container.innerHTML = filtered.map(p => `
    <div class="flex justify-between items-center bg-gray-50 p-3 rounded-xl border">
      <div>
        <h5 class="font-bold text-xs text-gray-900">${p.name}</h5>
        <span class="text-[10px] text-emerald-700 font-bold">₹${p.price} / ${p.unit}</span>
      </div>
      <button onclick="addToCart('${p.id}'); closeSearchModal();" class="bg-emerald-600 text-white font-bold text-xs px-3 py-1.5 rounded-lg">Add</button>
    </div>
  `).join("");
}

function switchTab(tabName) {
  ['home', 'offers', 'search', 'cart', 'profile'].forEach(t => {
    const el = document.getElementById(`nav-${t}`);
    if (el) el.className = "flex flex-col items-center text-gray-400 font-medium text-[11px]";
  });
  const active = document.getElementById(`nav-${tabName}`);
  if (active) active.className = "flex flex-col items-center text-emerald-600 font-bold text-[11px]";

  if (tabName === 'offers') {
    alert("🏷️ Promo Offer: Welcome Special - 15% off on daily milk subscriptions!");
  } else if (tabName === 'profile') {
    const name = localStorage.getItem("customer_name") || "Valued Customer";
    const email = localStorage.getItem("customer_email") || "Not signed in";
    const address = localStorage.getItem("customer_address") || "Jeddah / Kerala";
    alert(`👤 User Profile:\nName: ${name}\nEmail/Phone: ${email}\nAddress: ${address}\n\nYou can edit these details anytime.`);
  }
}

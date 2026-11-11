const API_BASE_URL = "https://backend.firstmilkventure.com";

document.addEventListener("DOMContentLoaded", () => {
  const today = new Date().toISOString().split('T')[0];
  const dateInput = document.getElementById("driver-date-input");
  if (dateInput) dateInput.value = today;

  checkDriverAuth();
});

function authenticateDriver() {
  const key = document.getElementById("driver-key-input").value.trim();
  const name = document.getElementById("driver-name-input").value.trim();
  const phone = document.getElementById("driver-phone-input").value.trim();
  const address = document.getElementById("driver-address-input").value.trim();
  const errorBox = document.getElementById("driver-login-error");
  errorBox.classList.add("hidden");

  if (!key) {
    errorBox.textContent = "Please enter your unique driver access key.";
    errorBox.classList.remove("hidden");
    return;
  }

  // Verify driver key with backend
  fetch(`${API_BASE_URL}/driver/deliveries`, {
    headers: { "x-driver-key": key }
  }).then(res => {
    if (res.ok) {
      localStorage.setItem("driver_key", key);
      if (name) localStorage.setItem("driver_name", name);
      if (phone) localStorage.setItem("driver_phone", phone);
      if (address) localStorage.setItem("driver_address", address);
      checkDriverAuth();
    } else {
      errorBox.textContent = "Invalid or unregistered Driver Access Key. Please check with admin.";
      errorBox.classList.remove("hidden");
    }
  }).catch(() => {
    // Fallback verification if offline
    if (key.length >= 4) {
      localStorage.setItem("driver_key", key);
      if (name) localStorage.setItem("driver_name", name);
      checkDriverAuth();
    } else {
      errorBox.textContent = "Invalid driver key.";
      errorBox.classList.remove("hidden");
    }
  });
}

function checkDriverAuth() {
  const key = localStorage.getItem("driver_key");
  const loginSection = document.getElementById("driver-login-section");
  const dashboard = document.getElementById("driver-dashboard");
  const authStatus = document.getElementById("driver-auth-status");

  if (key) {
    loginSection.classList.add("hidden");
    dashboard.classList.remove("hidden");
    authStatus.textContent = "Driver Active";
    
    const driverName = localStorage.getItem("driver_name") || "Delivery Driver";
    document.getElementById("driver-welcome-title").textContent = `Welcome, ${driverName}`;
    loadDriverDeliveries();
  } else {
    loginSection.classList.remove("hidden");
    dashboard.classList.add("hidden");
    authStatus.textContent = "Not Authenticated";
  }
}

function handleDriverLogout() {
  localStorage.removeItem("driver_key");
  localStorage.removeItem("driver_name");
  checkDriverAuth();
}

async function loadDriverDeliveries() {
  const key = localStorage.getItem("driver_key");
  const zoneId = document.getElementById("driver-zone-select").value;
  const dateInput = document.getElementById("driver-date-input").value;
  const container = document.getElementById("driver-deliveries-container");
  const countBadge = document.getElementById("driver-route-count");

  container.innerHTML = `<p class="text-xs text-gray-500">Loading delivery routes for ${zoneId} on ${dateInput}...</p>`;

  try {
    const res = await fetch(`${API_BASE_URL}/driver/deliveries?zone_id=${zoneId}&delivery_date=${dateInput}`, {
      headers: { "x-driver-key": key }
    });
    const deliveries = await res.json();

    countBadge.textContent = `${deliveries.length} Orders`;

    if (!deliveries.length) {
      container.innerHTML = `<p class="text-xs text-gray-500 italic">No delivery dispatches found for ${zoneId} on ${dateInput}.</p>`;
      return;
    }

    container.innerHTML = deliveries.map(d => `
      <div class="bg-white rounded-xl border border-amber-100 p-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 shadow-sm">
        <div>
          <div class="flex items-center space-x-2">
            <span class="font-bold text-gray-900 text-xs">Order #${d.order_number || d.id}</span>
            <span class="text-[10px] font-bold px-2 py-0.5 rounded uppercase ${d.status === 'delivered' ? 'bg-emerald-100 text-emerald-800' : 'bg-blue-100 text-blue-800'}">${d.status}</span>
          </div>
          <p class="text-xs font-semibold text-gray-700 mt-1">Customer: <span class="text-amber-700 font-bold">${d.user_email}</span></p>
          <p class="text-[11px] text-gray-500">Product Qty: ${d.quantity} unit(s) | Assigned Driver: <span class="font-bold text-gray-800">${d.assigned_driver || 'Assigned Driver'}</span></p>
        </div>
        <div class="flex items-center space-x-2">
          ${d.status !== 'delivered' ? `
            <button onclick="updateStatus('${d.id}', 'out_for_delivery')" class="text-xs bg-amber-600 hover:bg-amber-700 text-white font-bold px-3 py-2 rounded-lg shadow-sm">
              🚚 Out for Delivery
            </button>
            <button onclick="updateStatus('${d.id}', 'delivered')" class="text-xs bg-emerald-600 hover:bg-emerald-700 text-white font-bold px-3 py-2 rounded-lg shadow-sm">
              ✅ Mark Delivered
            </button>
          ` : `
            <span class="text-xs font-bold text-emerald-600 bg-emerald-50 px-3 py-2 rounded-lg border border-emerald-200">✓ Delivered Successfully</span>
          `}
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="text-xs text-red-500">Failed to load delivery route orders.</p>`;
  }
}

async function updateStatus(deliveryId, newStatus) {
  const key = localStorage.getItem("driver_key");
  try {
    const res = await fetch(`${API_BASE_URL}/driver/deliveries/${deliveryId}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "x-driver-key": key
      },
      body: JSON.stringify({ status: newStatus })
    });
    if (res.ok) {
      loadDriverDeliveries();
    } else {
      alert("Failed to update status.");
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

const API_BASE_URL = "https://backend.firstmilkventure.com";

document.addEventListener("DOMContentLoaded", () => {
  const today = new Date().toISOString().split('T')[0];
  const dateInput = document.getElementById("driver-date-input");
  if (dateInput) dateInput.value = today;

  checkDriverAuth();
});

function authenticateDriver() {
  const key = document.getElementById("driver-access-key").value.trim();
  const errorBox = document.getElementById("driver-login-error");
  errorBox.classList.add("hidden");

  if (!key) {
    errorBox.textContent = "Please enter your Driver Access Key.";
    errorBox.classList.remove("hidden");
    return;
  }

  if (key !== "DriverMilk2026" && key !== "Firstmilk3211234") {
    errorBox.textContent = "Invalid Driver Key. (Default: DriverMilk2026)";
    errorBox.classList.remove("hidden");
    return;
  }

  localStorage.setItem("driver_key", key);
  checkDriverAuth();
}

function checkDriverAuth() {
  const key = localStorage.getItem("driver_key");
  const loginSection = document.getElementById("driver-login-section");
  const dashboard = document.getElementById("driver-dashboard");
  const statusBadge = document.getElementById("driver-status-badge");

  if (key) {
    loginSection.classList.add("hidden");
    dashboard.classList.remove("hidden");
    statusBadge.textContent = "Driver Authorized";
    loadDriverRoutes();
  } else {
    loginSection.classList.remove("hidden");
    dashboard.classList.add("hidden");
    statusBadge.textContent = "Not Authenticated";
  }
}

function handleDriverLogout() {
  localStorage.removeItem("driver_key");
  checkDriverAuth();
}

async function loadDriverRoutes() {
  const key = localStorage.getItem("driver_key");
  const zoneId = document.getElementById("driver-zone-select").value;
  const dateInput = document.getElementById("driver-date-input").value;
  const container = document.getElementById("driver-routes-container");

  container.innerHTML = `<p class="text-xs text-gray-500">Loading route schedule for ${zoneId} on ${dateInput}...</p>`;

  try {
    const res = await fetch(`${API_BASE_URL}/driver/deliveries?zone_id=${zoneId}&delivery_date=${dateInput}`, {
      headers: { "x-driver-key": key }
    });
    const deliveries = await res.json();

    if (!deliveries || !deliveries.length) {
      container.innerHTML = `<p class="text-xs text-gray-500 italic">No delivery orders scheduled for ${zoneId} on ${dateInput}.</p>`;
      return;
    }

    container.innerHTML = deliveries.map(d => `
      <div class="bg-white rounded-xl border border-gray-200 p-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <div class="flex items-center space-x-2">
            <span class="font-bold text-gray-900 text-xs">Order #${d.order_number || d.id}</span>
            <span class="text-[10px] font-bold px-2 py-0.5 rounded uppercase ${d.status === 'delivered' ? 'bg-emerald-100 text-emerald-800' : d.status === 'out_for_delivery' ? 'bg-amber-100 text-amber-800' : 'bg-blue-100 text-blue-800'}">${d.status}</span>
          </div>
          <p class="text-xs font-semibold text-gray-700 mt-1">Customer Email: <span class="text-emerald-700 font-bold">${d.user_email}</span></p>
          <p class="text-[11px] text-gray-500">Qty: ${d.quantity} unit(s) | Zone: ${d.zone_id || 'Zone-1'} | Date: ${d.delivery_date}</p>
        </div>
        <div class="flex items-center space-x-2">
          <button onclick="updateDriverStatus('${d.id}', 'out_for_delivery')" class="text-xs bg-amber-50 hover:bg-amber-100 text-amber-800 font-bold px-3 py-2 rounded-lg border border-amber-300">
            🚚 Out for Delivery
          </button>
          <button onclick="updateDriverStatus('${d.id}', 'delivered')" class="text-xs bg-emerald-600 hover:bg-emerald-700 text-white font-bold px-3 py-2 rounded-lg">
            ✅ Mark Delivered
          </button>
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="text-xs text-red-500">Failed to load route orders.</p>`;
  }
}

async function updateDriverStatus(deliveryId, newStatus) {
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
      alert(`Order status updated to '${newStatus}'!`);
      loadDriverRoutes();
    } else {
      const err = await res.json();
      alert(`Error: ${err.detail || 'Failed to update status'}`);
    }
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

const API_BASE_URL = "https://backend.firstmilkventure.com";
let currentStaffRole = "admin";

document.addEventListener("DOMContentLoaded", () => {
  const today = new Date().toISOString().split('T')[0];
  const dateInput = document.getElementById("driver-date-input");
  if (dateInput) dateInput.value = today;

  checkStaffAuth();
});

function setStaffRole(role) {
  currentStaffRole = role;
  const adminBtn = document.getElementById("role-admin-btn");
  const driverBtn = document.getElementById("role-driver-btn");
  const keyInput = document.getElementById("staff-secret-key");

  if (role === 'admin') {
    adminBtn.className = "py-2.5 text-xs font-bold rounded-xl border border-emerald-600 bg-emerald-50 text-emerald-700";
    driverBtn.className = "py-2.5 text-xs font-bold rounded-xl border border-gray-200 bg-gray-50 text-gray-600";
    keyInput.value = "";
    keyInput.placeholder = "Enter Admin Access Key";
  } else {
    driverBtn.className = "py-2.5 text-xs font-bold rounded-xl border border-emerald-600 bg-emerald-50 text-emerald-700";
    adminBtn.className = "py-2.5 text-xs font-bold rounded-xl border border-gray-200 bg-gray-50 text-gray-600";
    keyInput.value = "";
    keyInput.placeholder = "Enter Driver Access Key";
  }
}

function authenticateStaff() {
  const key = document.getElementById("staff-secret-key").value.trim();
  const errorBox = document.getElementById("staff-error");
  errorBox.classList.add("hidden");

  if (!key) {
    errorBox.textContent = "Please enter staff access key.";
    errorBox.classList.remove("hidden");
    return;
  }

  if (currentStaffRole === 'admin' && key !== "Firstmilk3211234") {
    errorBox.textContent = "Invalid Admin Key. (Correct: Firstmilk3211234)";
    errorBox.classList.remove("hidden");
    return;
  }

  if (currentStaffRole === 'driver' && key !== "DriverMilk2026" && key !== "Firstmilk3211234") {
    errorBox.textContent = "Invalid Driver Key. (Correct: DriverMilk2026)";
    errorBox.classList.remove("hidden");
    return;
  }

  localStorage.setItem("staff_role", currentStaffRole);
  localStorage.setItem("staff_key", key);
  checkStaffAuth();
}

function checkStaffAuth() {
  const role = localStorage.getItem("staff_role");
  const key = localStorage.getItem("staff_key");

  const loginSection = document.getElementById("staff-login-section");
  const dashboard = document.getElementById("staff-dashboard");
  const roleBadge = document.getElementById("staff-role-badge");
  const adminWs = document.getElementById("workspace-admin");
  const driverWs = document.getElementById("workspace-driver");

  if (role && key) {
    loginSection.classList.add("hidden");
    dashboard.classList.remove("hidden");
    roleBadge.textContent = role === 'admin' ? "Category Admin" : "Delivery Operator";

    if (role === 'admin') {
      adminWs.classList.remove("hidden");
      driverWs.classList.add("hidden");
      loadAdminData();
    } else {
      adminWs.classList.add("hidden");
      driverWs.classList.remove("hidden");
      loadDriverRoutes();
    }
  } else {
    loginSection.classList.remove("hidden");
    dashboard.classList.add("hidden");
    roleBadge.textContent = "Not Authenticated";
  }
}

function handleStaffLogout() {
  localStorage.removeItem("staff_role");
  localStorage.removeItem("staff_key");
  checkStaffAuth();
}

async function loadAdminData() {
  const key = localStorage.getItem("staff_key");
  try {
    const products = await fetch(`${API_BASE_URL}/products`).then(r => r.json());
    document.getElementById("stat-products-count").textContent = products.length;

    const tableBody = document.getElementById("admin-product-table");
    tableBody.innerHTML = products.map(p => `
      <tr class="border-b border-gray-100 hover:bg-gray-50">
        <td class="py-3 font-bold text-gray-900">${p.name}<span class="block font-normal text-[10px] text-gray-400">${p.description || ''}</span></td>
        <td class="py-3">${p.unit || 'Liter'}</td>
        <td class="py-3 font-black text-emerald-700">₹${p.price}</td>
        <td class="py-3"><span class="px-2 py-0.5 rounded font-bold ${p.stock_quantity <= p.min_stock_threshold ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-800'}">${p.stock_quantity}</span></td>
        <td class="py-3 text-gray-500">${p.min_stock_threshold}</td>
        <td class="py-3 text-right">
          <button onclick="deleteProduct('${p.id}')" class="text-red-500 font-bold hover:underline">Delete</button>
        </td>
      </tr>
    `).join("");

    const logsRes = await fetch(`${API_BASE_URL}/admin/audit-logs`, {
      headers: { "x-admin-key": key }
    });
    if (logsRes.ok) {
      const logs = await logsRes.json();
      const logContainer = document.getElementById("audit-logs-container");
      if (!logs.length) {
        logContainer.innerHTML = `<p class="text-gray-400 italic">No audit logs recorded yet.</p>`;
      } else {
        logContainer.innerHTML = logs.map(l => `
          <div class="border-b border-gray-200 pb-1 mb-1">
            <span class="font-bold text-emerald-700">[${l.event_type || 'SYSTEM'}]</span> 
            <span class="text-gray-600">${JSON.stringify(l.details || l)}</span>
            <span class="text-[9px] text-gray-400 block">${l.timestamp || ''}</span>
          </div>
        `).join("");
      }
    }
  } catch (err) {
    console.error("Failed to load admin dashboard data:", err);
  }
}

async function triggerDailyDispatches() {
  const key = localStorage.getItem("staff_key");
  const targetDate = prompt("Enter target dispatch date (YYYY-MM-DD):", new Date().toISOString().split('T')[0]);
  if (!targetDate) return;

  try {
    const res = await fetch(`${API_BASE_URL}/admin/generate-deliveries`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-admin-key": key
      },
      body: JSON.stringify({ target_date: targetDate })
    });
    const data = await res.json();
    if (res.ok) {
      alert(`✅ Successfully generated ${data.result.generated_count || 0} orders for ${targetDate}! Emails dispatched.`);
      loadAdminData();
    } else {
      alert(`Error: ${data.detail || 'Failed to generate deliveries'}`);
    }
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

function openNewProductModal() {
  document.getElementById("product-modal").classList.remove("hidden");
}

function closeNewProductModal() {
  document.getElementById("product-modal").classList.add("hidden");
}

async function submitNewProduct() {
  const key = localStorage.getItem("staff_key");
  const name = document.getElementById("new-prod-name").value.trim();
  const description = document.getElementById("new-prod-desc").value.trim();
  const price = parseFloat(document.getElementById("new-prod-price").value);
  const unit = document.getElementById("new-prod-unit").value.trim();
  const stock_quantity = parseInt(document.getElementById("new-prod-stock").value);
  const min_stock_threshold = parseInt(document.getElementById("new-prod-threshold").value);

  if (!name || isNaN(price)) {
    alert("Please provide valid product name and price.");
    return;
  }

  try {
    const res = await fetch(`${API_BASE_URL}/admin/products`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-admin-key": key
      },
      body: JSON.stringify({ name, description, price, unit, stock_quantity, min_stock_threshold })
    });
    if (res.ok) {
      alert("Product added successfully!");
      closeNewProductModal();
      loadAdminData();
    } else {
      const err = await res.json();
      alert(`Error: ${err.detail || 'Failed to create product'}`);
    }
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

async function deleteProduct(productId) {
  if (!confirm("Are you sure you want to delete this product?")) return;
  const key = localStorage.getItem("staff_key");
  try {
    const res = await fetch(`${API_BASE_URL}/admin/products/${productId}`, {
      method: "DELETE",
      headers: { "x-admin-key": key }
    });
    if (res.ok) {
      alert("Product deleted successfully!");
      loadAdminData();
    } else {
      alert("Failed to delete product.");
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

function openBroadcastModal() {
  document.getElementById("broadcast-modal").classList.remove("hidden");
}

function closeBroadcastModal() {
  document.getElementById("broadcast-modal").classList.add("hidden");
}

async function submitBroadcast() {
  const key = localStorage.getItem("staff_key");
  const rawEmails = document.getElementById("broadcast-emails").value.trim();
  const subject = document.getElementById("broadcast-subject").value.trim();
  const custom_message = document.getElementById("broadcast-message").value.trim();

  if (!rawEmails) {
    alert("Please provide target email addresses.");
    return;
  }

  const target_emails = rawEmails.split(',').map(e => e.trim()).filter(e => e.includes('@'));

  try {
    const res = await fetch(`${API_BASE_URL}/admin/offers/broadcast`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-admin-key": key
      },
      body: JSON.stringify({ target_emails, subject, custom_message })
    });
    const data = await res.json();
    if (res.ok) {
      alert(data.message || "Broadcast sent successfully!");
      closeBroadcastModal();
    } else {
      alert(`Error: ${data.detail || 'Failed to broadcast offers'}`);
    }
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

async function loadDriverRoutes() {
  const key = localStorage.getItem("staff_key");
  const zoneId = document.getElementById("driver-zone-select").value;
  const dateInput = document.getElementById("driver-date-input").value;
  const container = document.getElementById("driver-routes-container");

  container.innerHTML = `<p class="text-xs text-gray-500">Loading routes for ${zoneId} on ${dateInput}...</p>`;

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
          <p class="text-xs font-semibold text-gray-700 mt-1">Customer: <span class="text-emerald-700 font-bold">${d.user_email}</span></p>
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
    container.innerHTML = `<p class="text-xs text-red-500">Failed to load driver route deliveries.</p>`;
  }
}

async function updateDriverStatus(deliveryId, newStatus) {
  const key = localStorage.getItem("staff_key");
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
      alert(`Delivery status updated to '${newStatus}'!`);
      loadDriverRoutes();
    } else {
      const err = await res.json();
      alert(`Error: ${err.detail || 'Failed to update status'}`);
    }
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

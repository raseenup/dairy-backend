const API_BASE_URL = "https://backend.firstmilkventure.com";

document.addEventListener("DOMContentLoaded", () => {
  const today = new Date().toISOString().split('T')[0];
  const dateInput = document.getElementById("admin-date-input");
  if (dateInput) dateInput.value = today;

  checkAdminAuth();
});

function authenticateAdmin() {
  const key = document.getElementById("staff-secret-key").value.trim();
  const errorBox = document.getElementById("staff-error");
  errorBox.classList.add("hidden");

  if (!key) {
    errorBox.textContent = "Please enter admin access key.";
    errorBox.classList.remove("hidden");
    return;
  }

  // Test admin key with backend
  fetch(`${API_BASE_URL}/admin/drivers`, {
    headers: { "x-admin-key": key }
  }).then(res => {
    if (res.ok) {
      localStorage.setItem("admin_key", key);
      checkAdminAuth();
    } else {
      errorBox.textContent = "Invalid Admin Key. Please verify and try again.";
      errorBox.classList.remove("hidden");
    }
  }).catch(() => {
    // Fallback verification if offline
    if (key === "Firstmilk3211234" || key.length >= 6) {
      localStorage.setItem("admin_key", key);
      checkAdminAuth();
    } else {
      errorBox.textContent = "Invalid Admin Key.";
      errorBox.classList.remove("hidden");
    }
  });
}

function checkAdminAuth() {
  const key = localStorage.getItem("admin_key");
  const loginSection = document.getElementById("staff-login-section");
  const dashboard = document.getElementById("staff-dashboard");
  const roleBadge = document.getElementById("staff-role-badge");

  if (key) {
    loginSection.classList.add("hidden");
    dashboard.classList.remove("hidden");
    roleBadge.textContent = "Category Admin";
    loadAdminData();
    loadAdminDeliveries();
  } else {
    loginSection.classList.remove("hidden");
    dashboard.classList.add("hidden");
    roleBadge.textContent = "Not Authenticated";
  }
}

function handleStaffLogout() {
  localStorage.removeItem("admin_key");
  checkAdminAuth();
}

async function loadAdminData() {
  const key = localStorage.getItem("admin_key");
  try {
    const products = await fetch(`${API_BASE_URL}/products`).then(r => r.json());
    document.getElementById("stat-products-count").textContent = products.length;

    const tableBody = document.getElementById("admin-product-table");
    tableBody.innerHTML = products.map(p => `
      <tr class="border-b border-gray-100 hover:bg-gray-50">
        <td class="py-3 font-bold text-gray-900">${p.name}<span class="block font-normal text-[10px] text-gray-400">${p.description || ''}</span></td>
        <td class="py-3">${p.unit || 'Liter'}</td>
        <td class="py-3 font-black text-emerald-700">₹${p.price}</td>
        <td class="py-3"><span class="px-2 py-0.5 rounded font-bold bg-emerald-100 text-emerald-800">${p.stock_quantity || 100}</span></td>
        <td class="py-3 text-right">
          <button onclick="deleteProduct('${p.id}')" class="text-red-500 font-bold hover:underline">Delete</button>
        </td>
      </tr>
    `).join("");
  } catch (err) {
    console.error("Failed to load admin data:", err);
  }
}

async function loadAdminDeliveries() {
  const key = localStorage.getItem("admin_key");
  const zoneId = document.getElementById("admin-zone-select").value;
  const dateInput = document.getElementById("admin-date-input").value;
  const container = document.getElementById("admin-deliveries-container");

  container.innerHTML = `<p class="text-xs text-gray-500">Loading deliveries for ${zoneId} on ${dateInput}...</p>`;

  try {
    const res = await fetch(`${API_BASE_URL}/driver/deliveries?zone_id=${zoneId}&delivery_date=${dateInput}`, {
      headers: { "x-driver-key": key }
    });
    const deliveries = await res.json();
    const driversRes = await fetch(`${API_BASE_URL}/admin/drivers`, {
      headers: { "x-admin-key": key }
    }).then(r => r.json()).catch(() => []);

    if (!deliveries || !deliveries.length) {
      container.innerHTML = `<p class="text-xs text-gray-500 italic">No orders scheduled for ${zoneId} on ${dateInput}.</p>`;
      return;
    }

    container.innerHTML = deliveries.map(d => `
      <div class="bg-white rounded-xl border border-gray-200 p-4 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <div class="flex items-center space-x-2">
            <span class="font-bold text-gray-900 text-xs">Order #${d.order_number || d.id}</span>
            <span class="text-[10px] font-bold px-2 py-0.5 rounded uppercase bg-blue-100 text-blue-800">${d.status}</span>
          </div>
          <p class="text-xs font-semibold text-gray-700 mt-1">Customer: <span class="text-emerald-700 font-bold">${d.user_email}</span></p>
          <p class="text-[11px] text-gray-500">Qty: ${d.quantity} unit(s) | Current Driver: <span class="font-bold text-amber-700">${d.assigned_driver || 'Primary Driver'}</span></p>
        </div>
        <div class="flex items-center space-x-2">
          <select id="assign-select-${d.id}" class="text-xs border border-gray-300 rounded-lg px-2 py-1.5 font-bold">
            ${driversRes.map(dr => `<option value="${dr.key}" ${d.assigned_driver === dr.name ? 'selected' : ''}>${dr.name} (${dr.key})</option>`).join('')}
          </select>
          <button onclick="reassignOrder('${d.id}')" class="text-xs bg-slate-900 text-white font-bold px-3 py-1.5 rounded-lg hover:bg-slate-800">
            Reassign Driver
          </button>
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="text-xs text-red-500">Failed to load delivery orders.</p>`;
  }
}

async function reassignOrder(deliveryId) {
  const key = localStorage.getItem("admin_key");
  const selectEl = document.getElementById(`assign-select-${deliveryId}`);
  const driverKey = selectEl.value;

  try {
    const res = await fetch(`${API_BASE_URL}/admin/deliveries/${deliveryId}/assign`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "x-admin-key": key
      },
      body: JSON.stringify({ driver_key: driverKey })
    });
    const data = await res.json();
    if (res.ok) {
      alert(`✅ ${data.message}`);
      loadAdminDeliveries();
    } else {
      alert(`Error: ${data.detail || 'Failed to reassign driver'}`);
    }
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

async function openDriverModal() {
  document.getElementById("driver-modal").classList.remove("hidden");
  loadDriversList();
}

function closeDriverModal() {
  document.getElementById("driver-modal").classList.add("hidden");
}

async function loadDriversList() {
  const key = localStorage.getItem("admin_key");
  const container = document.getElementById("drivers-list-admin");
  try {
    const res = await fetch(`${API_BASE_URL}/admin/drivers`, {
      headers: { "x-admin-key": key }
    });
    const drivers = await res.json();
    if (!drivers.length) {
      container.innerHTML = `<p class="text-gray-400 italic">No drivers configured yet.</p>`;
      return;
    }
    container.innerHTML = drivers.map(d => `
      <div class="flex justify-between items-center bg-white p-2.5 rounded-lg border">
        <div>
          <span class="font-bold text-gray-900">${d.name}</span>
          <span class="text-[11px] text-gray-500 block">Key: <code class="bg-gray-100 px-1 py-0.5 rounded font-mono font-bold text-emerald-700">${d.key}</code> | Phone: ${d.phone} | Zone: ${d.address}</span>
        </div>
        <button onclick="deleteDriver('${d.key}')" class="text-red-500 font-bold text-xs hover:underline">Remove</button>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="text-red-500 text-xs">Failed to load drivers list.</p>`;
  }
}

async function createNewDriver() {
  const key = localStorage.getItem("admin_key");
  const name = document.getElementById("new-driver-name").value.trim();
  const driver_key = document.getElementById("new-driver-key").value.trim();
  const phone = document.getElementById("new-driver-phone").value.trim();
  const address = document.getElementById("new-driver-address").value.trim();

  if (!name || !driver_key) {
    alert("Please provide driver name and unique secret key.");
    return;
  }

  try {
    const res = await fetch(`${API_BASE_URL}/admin/drivers`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-admin-key": key
      },
      body: JSON.stringify({ driver_key, name, phone, address })
    });
    if (res.ok) {
      alert("✅ Driver and unique access key created successfully!");
      document.getElementById("new-driver-name").value = "";
      document.getElementById("new-driver-key").value = "";
      document.getElementById("new-driver-phone").value = "";
      document.getElementById("new-driver-address").value = "";
      loadDriversList();
    } else {
      const err = await res.json();
      alert(`Error: ${err.detail || 'Failed to create driver'}`);
    }
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

async function deleteDriver(driverKey) {
  if (!confirm("Are you sure you want to remove this driver?")) return;
  const key = localStorage.getItem("admin_key");
  try {
    const res = await fetch(`${API_BASE_URL}/admin/drivers/${driverKey}`, {
      method: "DELETE",
      headers: { "x-admin-key": key }
    });
    if (res.ok) {
      loadDriversList();
    } else {
      alert("Failed to remove driver.");
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

function openChangeKeyModal() {
  document.getElementById("change-key-modal").classList.remove("hidden");
  document.getElementById("new-admin-key-input").value = "";
}

function closeChangeKeyModal() {
  document.getElementById("change-key-modal").classList.add("hidden");
}

async function submitNewAdminKey() {
  const currentKey = localStorage.getItem("admin_key");
  const newKey = document.getElementById("new-admin-key-input").value.trim();

  if (!newKey || newKey.length < 6) {
    alert("New admin key must be at least 6 characters long.");
    return;
  }

  try {
    const res = await fetch(`${API_BASE_URL}/admin/key`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "x-admin-key": currentKey
      },
      body: JSON.stringify({ new_admin_key: newKey })
    });
    if (res.ok) {
      alert("✅ Admin access key successfully updated! Please log in with your new key.");
      localStorage.setItem("admin_key", newKey);
      closeChangeKeyModal();
    } else {
      const err = await res.json();
      alert(`Error: ${err.detail || 'Failed to update admin key'}`);
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
  const key = localStorage.getItem("admin_key");
  const name = document.getElementById("new-prod-name").value.trim();
  const description = document.getElementById("new-prod-desc").value.trim();
  const price = parseFloat(document.getElementById("new-prod-price").value);
  const unit = document.getElementById("new-prod-unit").value.trim();

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
      body: JSON.stringify({ name, description, price, unit })
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
  const key = localStorage.getItem("admin_key");
  try {
    const res = await fetch(`${API_BASE_URL}/admin/products/${productId}`, {
      method: "DELETE",
      headers: { "x-admin-key": key }
    });
    if (res.ok) {
      loadAdminData();
    } else {
      alert("Failed to delete product.");
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function triggerDailyDispatches() {
  const key = localStorage.getItem("admin_key");
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
      alert(`✅ Successfully generated deliveries for ${targetDate}!`);
      loadAdminDeliveries();
    } else {
      alert(`Error: ${data.detail || 'Failed to generate deliveries'}`);
    }
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

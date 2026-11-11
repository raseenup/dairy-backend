const API_BASE_URL = "https://backend.firstmilkventure.com";

document.addEventListener("DOMContentLoaded", () => {
  checkAdminAuth();
});

function authenticateAdmin() {
  const key = document.getElementById("admin-secret-key").value.trim();
  const errorBox = document.getElementById("admin-login-error");
  errorBox.classList.add("hidden");

  if (!key) {
    errorBox.textContent = "Please enter Admin Access Key.";
    errorBox.classList.remove("hidden");
    return;
  }

  localStorage.setItem("admin_key", key);
  checkAdminAuth();
}

async function checkAdminAuth() {
  const key = localStorage.getItem("admin_key");
  const loginSection = document.getElementById("admin-login-section");
  const dashboard = document.getElementById("admin-dashboard");
  const errorBox = document.getElementById("admin-login-error");

  if (!key) {
    loginSection.classList.remove("hidden");
    dashboard.classList.add("hidden");
    return;
  }

  try {
    const res = await fetch(`${API_BASE_URL}/admin/drivers`, {
      headers: { "x-admin-key": key }
    });

    if (res.ok) {
      loginSection.classList.add("hidden");
      dashboard.classList.remove("hidden");
      loadAdminDashboardData();
    } else {
      localStorage.removeItem("admin_key");
      loginSection.classList.remove("hidden");
      dashboard.classList.add("hidden");
      errorBox.textContent = "Invalid Admin Secret Access Key. Access denied.";
      errorBox.classList.remove("hidden");
    }
  } catch (err) {
    errorBox.textContent = "Server connection error.";
    errorBox.classList.remove("hidden");
  }
}

function handleAdminLogout() {
  localStorage.removeItem("admin_key");
  checkAdminAuth();
}

async function changeAdminKey() {
  const newKey = document.getElementById("new-admin-key-input").value.trim();
  if (!newKey) return alert("Please enter valid new admin key.");

  const key = localStorage.getItem("admin_key");
  try {
    const res = await fetch(`${API_BASE_URL}/admin/key`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "x-admin-key": key },
      body: JSON.stringify({ new_admin_key: newKey })
    });
    if (res.ok) {
      alert("Admin Secret Access Key updated successfully! Please login with your new key.");
      handleAdminLogout();
    } else {
      alert("Failed to update Admin key.");
    }
  } catch (err) {
    alert("Error: " + err.message);
  }
}

async function loadAdminDashboardData() {
  const key = localStorage.getItem("admin_key");
  try {
    const driversRes = await fetch(`${API_BASE_URL}/admin/drivers`, {
      headers: { "x-admin-key": key }
    });
    const drivers = await driversRes.json();
    
    const tableContainer = document.getElementById("drivers-list-table");
    if (!drivers || !drivers.length) {
      tableContainer.innerHTML = `<p class="text-xs text-gray-500 italic">No custom driver codes generated yet.</p>`;
    } else {
      tableContainer.innerHTML = drivers.map(d => `
        <div class="flex items-center justify-between bg-gray-50 p-3 rounded-xl border border-gray-200 text-xs">
          <div>
            <span class="font-bold text-gray-900">${d.name}</span> (${d.phone})
            <span class="ml-2 font-mono text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded font-bold">Code: ${d.access_code}</span>
          </div>
          <button onclick="deleteDriver('${d.id}')" class="text-red-600 font-bold hover:underline">Remove</button>
        </div>
      `).join("");
    }
  } catch (err) {}
}

async function createNewDriver() {
  const name = document.getElementById("new-driver-name").value.trim();
  const phone = document.getElementById("new-driver-phone").value.trim();
  const access_code = document.getElementById("new-driver-code").value.trim();
  const key = localStorage.getItem("admin_key");

  if (!name || !access_code) return alert("Driver name and unique secret code are required.");

  try {
    const res = await fetch(`${API_BASE_URL}/admin/drivers`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-admin-key": key },
      body: JSON.stringify({ name, phone, address: "Jeddah", access_code })
    });
    if (res.ok) {
      alert("Driver access code created successfully!");
      loadAdminDashboardData();
    } else {
      alert("Failed to create driver.");
    }
  } catch (err) {
    alert("Error: " + err.message);
  }
}

async function deleteDriver(driverId) {
  if (!confirm("Are you sure you want to remove this driver?")) return;
  const key = localStorage.getItem("admin_key");
  try {
    await fetch(`${API_BASE_URL}/admin/drivers/${driverId}`, {
      method: "DELETE",
      headers: { "x-admin-key": key }
    });
    loadAdminDashboardData();
  } catch (err) {}
}

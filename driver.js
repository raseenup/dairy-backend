const API_BASE_URL = "https://backend.firstmilkventure.com";

document.addEventListener("DOMContentLoaded", () => {
  checkDriverAuth();
});

function authenticateDriver() {
  const key = document.getElementById("driver-access-key").value.trim();
  const errorBox = document.getElementById("driver-login-error");
  errorBox.classList.add("hidden");

  if (!key) {
    errorBox.textContent = "Please enter your unique driver secret code.";
    errorBox.classList.remove("hidden");
    return;
  }

  localStorage.setItem("driver_key", key);
  checkDriverAuth();
}

async function checkDriverAuth() {
  const key = localStorage.getItem("driver_key");
  const loginSection = document.getElementById("driver-login-section");
  const dashboard = document.getElementById("driver-dashboard");
  const errorBox = document.getElementById("driver-login-error");

  if (!key) {
    loginSection.classList.remove("hidden");
    dashboard.classList.add("hidden");
    return;
  }

  try {
    const res = await fetch(`${API_BASE_URL}/driver/deliveries`, {
      headers: { "x-driver-key": key }
    });

    if (res.ok) {
      loginSection.classList.add("hidden");
      dashboard.classList.remove("hidden");
      loadDriverRoutes();
    } else {
      localStorage.removeItem("driver_key");
      loginSection.classList.remove("hidden");
      dashboard.classList.add("hidden");
      errorBox.textContent = "Invalid driver access key provided. Please contact Admin.";
      errorBox.classList.remove("hidden");
    }
  } catch (err) {
    errorBox.textContent = "Server connection error.";
    errorBox.classList.remove("hidden");
  }
}

function handleDriverLogout() {
  localStorage.removeItem("driver_key");
  checkDriverAuth();
}

async function loadDriverRoutes() {
  const key = localStorage.getItem("driver_key");
  const container = document.getElementById("driver-routes-container");
  try {
    const res = await fetch(`${API_BASE_URL}/driver/deliveries`, {
      headers: { "x-driver-key": key }
    });
    const deliveries = await res.json();
    if (!deliveries || !deliveries.length) {
      container.innerHTML = `<p class="text-xs text-gray-500 italic">No assigned deliveries for today.</p>`;
      return;
    }
    container.innerHTML = deliveries.map(d => `
      <div class="bg-white rounded-xl border border-gray-200 p-4 flex justify-between items-center text-xs">
        <div>
          <span class="font-bold text-gray-900">Order #${d.id}</span> - <span class="text-emerald-700 font-bold">${d.user_email}</span>
          <p class="text-[11px] text-gray-500">Qty: ${d.quantity} unit(s) | Status: ${d.status}</p>
        </div>
      </div>
    `).join("");
  } catch (err) {}
}

async function saveDriverProfile() {
  const name = document.getElementById("driver-self-name").value.trim();
  const phone = document.getElementById("driver-self-phone").value.trim();
  const address = document.getElementById("driver-self-address").value.trim();
  const key = localStorage.getItem("driver_key");

  try {
    await fetch(`${API_BASE_URL}/driver/profile`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "x-driver-key": key },
      body: JSON.stringify({ name, phone, address })
    });
    alert("Driver profile updated successfully!");
  } catch (err) {
    alert("Failed to save details: " + err.message);
  }
}

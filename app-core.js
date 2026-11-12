// Shared LocalStorage State Manager for First Milk Portals

function generateOTP() {
  return Math.floor(1000 + Math.random() * 9000).toString();
}

const defaultOrders = [
  {
    id: 8,
    customerName: "Raseen Rasi",
    contactNumber: "+966 50 860 0262",
    productName: "Fresh Farm Cow Milk (1L)",
    quantity: 1,
    orderType: "Subscribed",
    paymentStatus: "Paid Online",
    codAmount: 0.00,
    location: "Hail Street, Ruwais, Jeddah",
    otp: "4821",
    status: "pending",
    deliveredAt: null
  },
  {
    id: 9,
    customerName: "Faisal Al-Qahtani",
    contactNumber: "+966 55 391 4402",
    productName: "Organic A2 Full Cream Milk (2L)",
    quantity: 2,
    orderType: "Single Purchase",
    paymentStatus: "COD Due",
    codAmount: 36.00,
    location: "Al Rawdah District, Jeddah",
    otp: "7190",
    status: "pending",
    deliveredAt: null
  },
  {
    id: 10,
    customerName: "Nadia Mansoor",
    contactNumber: "+966 56 772 1904",
    productName: "Fresh Farm Cow Milk (1L) + Laban",
    quantity: 3,
    orderType: "Subscribed",
    paymentStatus: "COD Due",
    codAmount: 28.50,
    location: "Al Salamah, Street 14, Jeddah",
    otp: "3319",
    status: "pending",
    deliveredAt: null
  }
];

const defaultLedger = [
  { type: "expense", title: "Morning Fuel Top-up", amount: 35.00, time: "07:45 AM" }
];

const defaultLogs = [
  { type: "Clock In", start: "07:00 AM", end: "Active", duration: "Shift Started" },
  { type: "Break", start: "09:15 AM", end: "09:30 AM", duration: "15 mins" }
];

const defaultAttendance = [
  { date: "2026-11-10", clockIn: "07:00 AM", clockOut: "03:30 PM", breaksCount: 2, breakMins: 30, netHours: "8.00 hrs" },
  { date: "2026-11-11", clockIn: "06:55 AM", clockOut: "03:40 PM", breaksCount: 1, breakMins: 20, netHours: "8.42 hrs" },
  { date: "2026-11-12", clockIn: "07:00 AM", clockOut: "Active", breaksCount: 1, breakMins: 15, netHours: "In Progress" }
];

function getOrders() {
  const data = localStorage.getItem('fm_orders');
  if (!data) {
    localStorage.setItem('fm_orders', JSON.stringify(defaultOrders));
    return defaultOrders;
  }
  return JSON.parse(data);
}
function saveOrders(orders) {
  localStorage.setItem('fm_orders', JSON.stringify(orders));
}

function getLedger() {
  const data = localStorage.getItem('fm_ledger');
  if (!data) {
    localStorage.setItem('fm_ledger', JSON.stringify(defaultLedger));
    return defaultLedger;
  }
  return JSON.parse(data);
}
function addLedgerItem(item) {
  const ledger = getLedger();
  ledger.unshift(item);
  localStorage.setItem('fm_ledger', JSON.stringify(ledger));
}

function getLogs() {
  const data = localStorage.getItem('fm_logs');
  if (!data) {
    localStorage.setItem('fm_logs', JSON.stringify(defaultLogs));
    return defaultLogs;
  }
  return JSON.parse(data);
}
function addLog(log) {
  const logs = getLogs();
  logs.unshift(log);
  localStorage.setItem('fm_logs', JSON.stringify(logs));
}

function getAttendance() {
  const data = localStorage.getItem('fm_attendance');
  if (!data) {
    localStorage.setItem('fm_attendance', JSON.stringify(defaultAttendance));
    return defaultAttendance;
  }
  return JSON.parse(data);
}

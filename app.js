(() => {
  "use strict";

  const configuredApi = document.querySelector('meta[name="nammaurucab-api-url"]')?.content || "";
  const API_ROOT = configuredApi.replace(/\/+$/, "");
  const TOKEN_KEY = "nammaurucab.accessToken";
  const USER_KEY = "nammaurucab.user";
  const money = value => `₹${Number(value || 0).toLocaleString("en-IN")}`;
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
  let activeUser = null;
  let accessToken = "";
  let deferredBooking = false;
  let pendingVendorVehicle = null;

  function showNotice(message, kind = "error") {
    const pane = document.getElementById("appNotice");
    if (!pane) return;
    pane.textContent = message;
    pane.className = `mb-4 rounded-xl px-4 py-3 text-sm font-semibold ${kind === "success" ? "bg-emerald-50 text-emerald-800 border border-emerald-200" : "bg-rose-50 text-rose-800 border border-rose-200"}`;
    pane.classList.remove("hidden");
    pane.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function setStatus(message = "") {
    const element = document.getElementById("appStatus");
    if (element) element.textContent = message;
  }

  async function request(path, options = {}) {
    if (!API_ROOT) throw new Error("The booking service URL is not configured.");
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    let response;
    try {
      response = await fetch(`${API_ROOT}${path}`, {
        ...options,
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body)
      });
    } catch {
      throw new Error("The booking service is not reachable yet. Please try again shortly.");
    }
    const result = response.status === 204 ? {} : await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error?.message || `The request failed (${response.status}).`);
    return result.data;
  }

  function setSession(user, token) {
    activeUser = user;
    accessToken = token;
    if (user && token) {
      localStorage.setItem(USER_KEY, JSON.stringify(user));
      localStorage.setItem(TOKEN_KEY, token);
    } else {
      localStorage.removeItem(USER_KEY);
      localStorage.removeItem(TOKEN_KEY);
    }
    const button = document.getElementById("accountButton");
    if (button) button.textContent = user ? user.name.split(/\s+/)[0] : "Sign in";
  }

  function ensurePanes() {
    if (document.getElementById("appAccountPane")) return;
    document.body.insertAdjacentHTML("beforeend", `
      <div id="appAccountPane" class="fixed inset-0 z-[70] hidden items-center justify-center bg-slate-950/70 p-3 sm:p-6" role="dialog" aria-modal="true" aria-labelledby="appPaneTitle">
        <div class="relative flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl">
          <div class="flex items-center justify-between border-b border-slate-200 px-5 py-4 sm:px-7">
            <div><h2 id="appPaneTitle" class="font-display text-xl font-extrabold text-slate-900">Your Namma Uru Cab account</h2><p id="appStatus" class="mt-1 text-xs text-slate-500">Secure account and booking access</p></div>
            <button type="button" onclick="closeAppPane()" class="rounded-lg px-3 py-2 text-xl text-slate-500 hover:bg-slate-100" aria-label="Close">&times;</button>
          </div>
          <div id="appPaneBody" class="overflow-y-auto p-5 sm:p-7"><div id="appNotice" class="hidden"></div></div>
        </div>
      </div>
      <div id="appContactPane" class="fixed inset-0 z-[71] hidden items-center justify-center bg-slate-950/70 p-3 sm:p-6" role="dialog" aria-modal="true" aria-labelledby="contactPaneTitle">
        <div class="relative w-full max-w-lg rounded-3xl bg-white p-6 shadow-2xl sm:p-8">
          <button type="button" onclick="closeContactPane()" class="absolute right-4 top-3 rounded-lg px-3 py-2 text-xl text-slate-500 hover:bg-slate-100" aria-label="Close">&times;</button>
          <h2 id="contactPaneTitle" class="font-display text-xl font-extrabold text-slate-900">Contact Namma Uru Cab</h2>
          <p class="mt-1 text-sm text-slate-500">Send a message to our team. We will follow up using your contact details.</p>
          <div id="contactNotice" class="mt-4 hidden rounded-xl border px-4 py-3 text-sm font-semibold"></div>
          <form id="contactForm" class="mt-5 space-y-3">
            <label class="block text-xs font-bold text-slate-700">Name<input name="name" required maxlength="120" autocomplete="name" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"></label>
            <label class="block text-xs font-bold text-slate-700">Email<input name="email" type="email" required maxlength="254" autocomplete="email" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"></label>
            <label class="block text-xs font-bold text-slate-700">Phone (optional)<input name="phone" type="tel" maxlength="20" autocomplete="tel" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"></label>
            <label class="block text-xs font-bold text-slate-700">Message<textarea name="message" required maxlength="4000" rows="4" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"></textarea></label>
            <button class="w-full rounded-xl bg-brand-blue px-4 py-3 text-sm font-extrabold text-white hover:bg-brand-bluedark">Send message</button>
          </form>
        </div>
      </div>`);
    const auth = document.getElementById("appAccountPane");
    const contact = document.getElementById("appContactPane");
    [auth, contact].forEach(pane => pane.addEventListener("click", event => {
      if (event.target === pane) pane.classList.add("hidden");
    }));
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") {
        auth.classList.add("hidden");
        contact.classList.add("hidden");
      }
    });
    document.getElementById("contactForm").addEventListener("submit", submitContact);
  }

  function openPane() {
    ensurePanes();
    document.getElementById("appNotice").classList.add("hidden");
    document.getElementById("appAccountPane").classList.remove("hidden");
    document.getElementById("appAccountPane").classList.add("flex");
  }

  function showAuthForm(kind = "signin") {
    openPane();
    document.getElementById("appPaneTitle").textContent = kind === "signin" ? "Sign in to Namma Uru Cab" : "Create your account";
    setStatus("Book rides and manage your account");
    document.getElementById("appPaneBody").innerHTML = `
      <div id="appNotice" class="hidden"></div>
      <div class="mb-5 grid grid-cols-2 gap-2 rounded-xl bg-slate-100 p-1">
        <button type="button" onclick="showCabAuth('signin')" class="rounded-lg px-3 py-2 text-sm font-bold ${kind === "signin" ? "bg-white text-brand-blue shadow" : "text-slate-600"}">Sign in</button>
        <button type="button" onclick="showCabAuth('signup')" class="rounded-lg px-3 py-2 text-sm font-bold ${kind !== "signin" ? "bg-white text-brand-blue shadow" : "text-slate-600"}">Create account</button>
      </div>
      <form id="accountForm" class="space-y-3">
        ${kind === "signin" ? "" : `<label class="block text-xs font-bold text-slate-700">Account type<select name="role" id="accountRole" onchange="updateSignupFields()" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"><option value="customer">Customer</option><option value="vendor">Cab or fleet owner</option><option value="driver">Driver</option></select></label>`}
        ${kind === "signin" ? "" : `<label class="block text-xs font-bold text-slate-700">Full name<input name="name" required maxlength="120" autocomplete="name" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"></label>`}
        <label class="block text-xs font-bold text-slate-700">Email<input name="email" type="email" required maxlength="254" autocomplete="email" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"></label>
        ${kind === "signin" ? "" : `<label class="block text-xs font-bold text-slate-700">Phone<input name="phone" type="tel" required maxlength="20" autocomplete="tel" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"></label>`}
        <label class="block text-xs font-bold text-slate-700">Password<input name="password" type="password" required minlength="12" maxlength="256" autocomplete="${kind === "signin" ? "current-password" : "new-password"}" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"><span class="mt-1 block text-[11px] font-normal text-slate-500">Use at least 12 characters.</span></label>
        <div id="signupExtraFields"></div>
        <button class="w-full rounded-xl bg-brand-blue px-4 py-3 text-sm font-extrabold text-white hover:bg-brand-bluedark">${kind === "signin" ? "Sign in" : "Create account"}</button>
      </form>
      ${kind === "signup"
        ? `<p class="mt-3 text-xs leading-relaxed text-slate-500">Vendor and driver accounts require administrator approval before cabs can be listed or a driver can be assigned.</p>`
        : `<button type="button" onclick="showCabAuth('signup')" class="mt-4 text-sm font-bold text-brand-blue hover:underline">New here? Create an account</button>`}`;
    document.getElementById("accountForm").addEventListener("submit", submitAccount);
    if (kind === "signup") updateSignupFields();
  }

  function updateSignupFields() {
    const role = document.getElementById("accountRole")?.value || "customer";
    const extra = document.getElementById("signupExtraFields");
    if (!extra) return;
    const city = '<label class="block text-xs font-bold text-slate-700">Base city<input name="base_city" required maxlength="120" value="Bengaluru" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"></label>';
    extra.innerHTML = role === "vendor"
      ? `<div class="space-y-3"><label class="block text-xs font-bold text-slate-700">Business or fleet name<input name="business_name" required maxlength="120" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"></label><label class="block text-xs font-bold text-slate-700">Partner type<select name="partner_type" class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm"><option value="single">Single cab owner</option><option value="fleet">Fleet operator</option></select></label>${city}</div>`
      : role === "driver" ? city : "";
  }

  async function submitAccount(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    const isSignup = Boolean(data.role);
    const endpoint = isSignup
      ? `/auth/${data.role === "customer" ? "signup" : `${data.role}-signup`}`
      : "/auth/signin";
    const button = form.querySelector("button[type=submit], button:not([type])");
    if (button) { button.disabled = true; button.textContent = "Please wait..."; }
    try {
      const result = await request(endpoint, { method: "POST", body: data });
      setSession(result.user, result.access_token);
      let cabSubmitted = false;
      if (isSignup && result.user.role === "vendor" && pendingVendorVehicle) {
        try {
          await request("/vendors/me/vehicles", { method: "POST", body: pendingVendorVehicle });
          cabSubmitted = true;
        } catch (error) {
          renderDashboard();
          showNotice(`Your vendor account was created, but the cab could not be saved: ${error.message}. Sign in and add the vehicle from your account.`);
          pendingVendorVehicle = null;
          return;
        }
        pendingVendorVehicle = null;
      }
      renderDashboard();
      showNotice(cabSubmitted
        ? "Vendor account and cab listing submitted. The account and vehicle must be approved before the cab appears in search."
        : isSignup && result.user.role !== "customer"
        ? "Your account is created and waiting for administrator approval."
        : "You are signed in.", "success");
      if (deferredBooking && result.user.role === "customer") {
        deferredBooking = false;
        await searchCabs();
      }
    } catch (error) {
      showNotice(error.message);
      if (button) { button.disabled = false; button.textContent = isSignup ? "Create account" : "Sign in"; }
    }
  }

  function openAccountPane() {
    if (activeUser && accessToken) {
      openPane();
      renderDashboard();
    } else {
      showAuthForm("signin");
    }
  }

  async function renderDashboard() {
    if (!activeUser || !accessToken) return showAuthForm("signin");
    document.getElementById("appPaneTitle").textContent = `Welcome, ${activeUser.name}`;
    setStatus(`${activeUser.role} account · ${activeUser.email}`);
    const body = document.getElementById("appPaneBody");
    body.innerHTML = `<div id="appNotice" class="hidden"></div><div class="mb-5 flex flex-wrap gap-2"><button type="button" onclick="refreshDashboard()" class="rounded-lg border border-slate-300 px-3 py-2 text-xs font-bold">Refresh</button><button type="button" onclick="loadMyBookings()" class="rounded-lg border border-slate-300 px-3 py-2 text-xs font-bold">My bookings</button><button type="button" onclick="loadMyNotifications()" class="rounded-lg border border-slate-300 px-3 py-2 text-xs font-bold">Notifications</button><button type="button" onclick="signOutCab()" class="ml-auto rounded-lg border border-rose-200 px-3 py-2 text-xs font-bold text-rose-700">Sign out</button></div><div id="dashboardContent" class="space-y-4"><div class="rounded-2xl bg-blue-50 p-4 text-sm text-blue-900">Loading your account...</div></div>`;
    const content = document.getElementById("dashboardContent");
    try {
      if (activeUser.role === "vendor") {
        const [profile, vehicles] = await Promise.all([request("/vendors/me"), request("/vendors/me/vehicles")]);
        content.innerHTML = `<div class="rounded-2xl border border-slate-200 p-4"><h3 class="font-bold">${escapeHtml(profile.business_name)}</h3><p class="mt-1 text-sm text-slate-600">${escapeHtml(profile.base_city)} · <strong>${escapeHtml(profile.status)}</strong> approval</p><form id="vehicleForm" class="mt-4 grid gap-3 sm:grid-cols-2"><select name="vehicle_class" class="rounded-xl border border-slate-300 px-3 py-2 text-sm"><option value="hatchback">Hatchback</option><option value="sedan">Sedan</option><option value="suv">SUV</option><option value="crysta">Innova Crysta</option></select><input name="make_model" required maxlength="100" placeholder="Vehicle model and year" class="rounded-xl border border-slate-300 px-3 py-2 text-sm"><input name="registration_number" required maxlength="20" placeholder="Commercial plate number" class="rounded-xl border border-slate-300 px-3 py-2 text-sm"><input name="seats" required type="number" min="1" max="16" value="4" placeholder="Seats" class="rounded-xl border border-slate-300 px-3 py-2 text-sm"><input name="rate_per_km" required type="number" min="1" max="10000" placeholder="Rate per km (₹)" class="rounded-xl border border-slate-300 px-3 py-2 text-sm"><input name="driver_allowance" required type="number" min="0" max="100000" value="400" placeholder="Driver allowance (₹)" class="rounded-xl border border-slate-300 px-3 py-2 text-sm"><button class="rounded-xl bg-brand-blue px-3 py-2 text-sm font-bold text-white sm:col-span-2">Add a cab for review</button></form></div><div class="space-y-2"><h3 class="font-bold">Your cabs</h3>${vehicles.length ? vehicles.map(vehicleCard).join("") : '<p class="text-sm text-slate-500">No cabs yet. Add a cab above; an administrator must approve it before search.</p>'}</div>`;
        document.getElementById("vehicleForm").addEventListener("submit", addVehicle);
      } else if (activeUser.role === "driver") {
        const profile = await request("/drivers/me");
        content.innerHTML = `<div class="rounded-2xl border border-slate-200 p-4"><h3 class="font-bold">Driver profile</h3><p class="mt-1 text-sm text-slate-600">Base city: ${escapeHtml(profile.base_city)} · Approval: <strong>${escapeHtml(profile.status)}</strong></p></div><div class="text-sm text-slate-600">Your assigned rides appear under <button type="button" onclick="loadMyBookings()" class="font-bold text-brand-blue underline">My bookings</button>.</div>`;
      } else if (activeUser.role === "admin") {
        await loadAdminDashboard();
      } else {
        await loadMyBookings();
      }
    } catch (error) {
      showNotice(error.message);
      if (error.message.includes("Session is invalid or expired")) {
        setSession(null, "");
        showAuthForm("signin");
      }
    }
  }

  function vehicleCard(vehicle) {
    return `<article class="rounded-xl border border-slate-200 p-3 text-sm"><div class="flex flex-wrap justify-between gap-2"><strong>${escapeHtml(vehicle.make_model)} · ${escapeHtml(vehicle.registration_number)}</strong><span class="rounded-full bg-amber-50 px-2 py-1 text-xs font-bold">${escapeHtml(vehicle.status)}</span></div><p class="mt-1 text-slate-600">${escapeHtml(vehicle.vehicle_class)} · ${vehicle.seats} seats · ${money(vehicle.rate_per_km)}/km + ${money(vehicle.driver_allowance)} driver allowance</p></article>`;
  }

  async function addVehicle(event) {
    event.preventDefault();
    try {
      await request("/vendors/me/vehicles", { method: "POST", body: Object.fromEntries(new FormData(event.currentTarget).entries()) });
      await renderDashboard();
      showNotice("Cab submitted. It will appear in search once approved.", "success");
    } catch (error) { showNotice(error.message); }
  }

  async function loadMyBookings() {
    const content = document.getElementById("dashboardContent");
    if (!content) return;
    try {
      const path = activeUser.role === "driver" ? "/drivers/me/bookings" : "/bookings";
      const bookings = await request(path);
      content.innerHTML = `<h3 class="font-bold">Your bookings</h3>${bookings.length ? bookings.map(bookingCard).join("") : '<p class="rounded-xl bg-slate-50 p-4 text-sm text-slate-600">No bookings yet. Search for a cab to get started.</p>'}`;
    } catch (error) { showNotice(error.message); }
  }

  function bookingCard(booking) {
    const actions = booking.status === "awaiting_customer_confirmation" && activeUser.role === "customer"
      ? `<button type="button" onclick="confirmCabBooking('${booking.id}')" class="mt-3 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white">Confirm quoted fare ${money(booking.quoted_fare)}</button>`
      : ["requested", "awaiting_customer_confirmation", "confirmed", "assigned"].includes(booking.status) && ["customer", "admin"].includes(activeUser.role)
        ? `<button type="button" onclick="cancelCabBooking('${booking.id}')" class="mt-3 rounded-lg border border-rose-200 px-3 py-2 text-xs font-bold text-rose-700">Cancel booking</button>` : "";
    return `<article class="rounded-2xl border border-slate-200 p-4"><div class="flex flex-wrap justify-between gap-2"><strong>${escapeHtml(booking.pickup_city)} → ${escapeHtml(booking.drop_city)}</strong><span class="rounded-full bg-blue-50 px-2 py-1 text-xs font-bold">${escapeHtml(booking.status.replaceAll("_", " "))}</span></div><p class="mt-2 text-sm text-slate-600">${escapeHtml(booking.vehicle.make_model)} · ${escapeHtml(booking.trip_type)} · ${new Date(booking.pickup_at).toLocaleString()}</p><p class="mt-1 text-xs text-slate-500">Reference ${escapeHtml(booking.id)}${booking.quoted_fare ? ` · Quoted ${money(booking.quoted_fare)}` : ""}${booking.driver_id ? " · Driver assigned" : ""}</p>${actions}</article>`;
  }

  async function loadMyNotifications() {
    const content = document.getElementById("dashboardContent");
    if (!content) return;
    try {
      const notices = await request("/notifications?limit=50");
      content.innerHTML = `<h3 class="font-bold">Notifications</h3>${notices.length ? notices.map(notice => `<article class="flex items-start justify-between gap-3 rounded-xl border border-slate-200 p-3"><div><p class="text-sm">${escapeHtml(notice.message)}</p><p class="mt-1 text-xs text-slate-500">${new Date(notice.created_at).toLocaleString()}</p></div>${notice.read_at ? '<span class="text-xs text-emerald-700">Read</span>' : `<button type="button" onclick="readCabNotification('${notice.id}')" class="shrink-0 text-xs font-bold text-brand-blue">Mark read</button>`}</article>`).join("") : '<p class="text-sm text-slate-500">No notifications yet.</p>'}`;
    } catch (error) { showNotice(error.message); }
  }

  async function loadAdminDashboard() {
    const content = document.getElementById("dashboardContent");
    const [bookings, vendors, drivers, vehicles] = await Promise.all([
      request("/admin/bookings?limit=50"), request("/admin/vendors?limit=50"), request("/admin/drivers?limit=50"), request("/admin/vehicles?limit=50")
    ]);
    const review = (items, resource, title) => `<section class="space-y-2"><h3 class="font-bold">${title}</h3>${items.length ? items.map(item => {
      const id = resource === "vehicles" ? item.id : item.user_id;
      const label = resource === "vendors" ? item.business_name : resource === "drivers" ? item.name : `${item.make_model} · ${item.registration_number}`;
      return `<article class="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 p-3"><div><strong class="text-sm">${escapeHtml(label)}</strong><p class="text-xs text-slate-500">${escapeHtml(item.status)}${item.email ? ` · ${escapeHtml(item.email)}` : ""}${item.base_city ? ` · ${escapeHtml(item.base_city)}` : ""}</p></div><div class="flex gap-1">${["approved", "rejected", "suspended"].map(status => `<button type="button" onclick="reviewCabAccount('${resource}','${id}','${status}')" class="rounded-lg border border-slate-200 px-2 py-1 text-[11px] font-bold">${status}</button>`).join("")}</div></article>`;
    }).join("") : '<p class="text-sm text-slate-500">None yet.</p>'}</section>`;
    const approvedDrivers = drivers.filter(driver => driver.status === "approved");
    content.innerHTML = `<section class="space-y-2"><h3 class="font-bold">Booking requests and dispatch</h3>${bookings.length ? bookings.map(booking => `<article class="rounded-xl border border-slate-200 p-3"><div class="flex flex-wrap justify-between gap-2"><strong class="text-sm">${escapeHtml(booking.pickup_city)} → ${escapeHtml(booking.drop_city)}</strong><span class="text-xs font-bold">${escapeHtml(booking.status)}</span></div><p class="mt-1 text-xs text-slate-600">${escapeHtml(booking.vehicle.make_model)} · ${new Date(booking.pickup_at).toLocaleString()} · ${escapeHtml(booking.customer?.name || "")} ${escapeHtml(booking.customer?.phone || "")}</p>${booking.status === "requested" ? `<div class="mt-3 flex gap-2"><input id="fare-${booking.id}" type="number" min="1" max="10000000" placeholder="Quote (₹)" class="w-32 rounded-lg border border-slate-300 px-2 py-1 text-sm"><button type="button" onclick="quoteCabBooking('${booking.id}')" class="rounded-lg bg-brand-blue px-3 py-1 text-xs font-bold text-white">Send quote</button><button type="button" onclick="adminBookingAction('${booking.id}','reject')" class="rounded-lg border border-rose-200 px-3 py-1 text-xs font-bold text-rose-700">Reject</button></div>` : ""}${booking.status === "confirmed" ? approvedDrivers.length ? `<div class="mt-3 flex flex-wrap gap-2"><select id="driver-${booking.id}" class="rounded-lg border border-slate-300 px-2 py-1 text-sm">${approvedDrivers.map(driver => `<option value="${driver.user_id}">${escapeHtml(driver.name)} · ${escapeHtml(driver.base_city)}</option>`).join("")}</select><button type="button" onclick="assignCabDriver('${booking.id}',document.getElementById('driver-${booking.id}').value)" class="rounded-lg bg-brand-blue px-3 py-1 text-xs font-bold text-white">Assign driver</button></div>` : '<p class="mt-3 text-xs text-amber-700">No approved drivers are available.</p>' : ""}${booking.status === "assigned" ? `<button type="button" onclick="adminBookingAction('${booking.id}','complete')" class="mt-3 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white">Complete ride</button>` : ""}</article>`).join("") : '<p class="text-sm text-slate-500">No bookings yet.</p>'}</section>${review(vendors, "vendors", "Vendor approvals")}${review(drivers, "drivers", "Driver approvals")}${review(vehicles, "vehicles", "Cab approvals")}`;
  }

  async function searchCabs() {
    const pickup = document.getElementById("custPickup");
    const drop = document.getElementById("custDrop");
    const date = document.getElementById("custDate");
    const time = document.getElementById("custTime");
    if (!pickup?.reportValidity() || (customerTripType !== "hourly" && !drop.reportValidity()) || !date?.reportValidity() || !time?.reportValidity()) return;
    const localPickup = new Date(`${date.value}T${time.value}`);
    if (Number.isNaN(localPickup.valueOf()) || localPickup <= new Date()) {
      showSearchMessage("Choose a pickup time in the future.");
      return;
    }
    const packageMinutes = { "4hr40km": 240, "8hr80km": 480, "12hr120km": 720 };
    const duration = customerTripType === "hourly" ? packageMinutes[document.getElementById("custPackage").value] : customerTripType === "round" ? 720 : customerTripType === "airport" ? 120 : 240;
    const pickupAt = localPickup.toISOString();
    const params = new URLSearchParams({ pickup_city: pickup.value.trim(), pickup_at: pickupAt, duration_minutes: String(duration), vehicle_class: customerSelectedVehicle });
    showSearchMessage("Checking approved cabs and live availability...");
    try {
      const vehicles = await request(`/cabs/search?${params}`);
      if (!vehicles.length) {
        showSearchMessage("No approved cab is available for this vehicle class and pickup time. Try another time or choose a different class.");
        return;
      }
      renderCabs(vehicles, { pickup: pickup.value.trim(), drop: customerTripType === "hourly" ? "Local hourly rental" : drop.value.trim(), pickupAt, dropoffAt: new Date(localPickup.valueOf() + duration * 60000).toISOString(), tripType: customerTripType });
    } catch (error) {
      showSearchMessage(error.message);
    }
  }

  function showSearchMessage(message) {
    let target = document.getElementById("liveCabResults");
    if (!target) {
      const fareStrip = document.getElementById("custFareDisplay")?.closest(".mt-5");
      if (!fareStrip) return;
      fareStrip.insertAdjacentHTML("afterend", '<div id="liveCabResults" class="mt-4 space-y-3" aria-live="polite"></div>');
      target = document.getElementById("liveCabResults");
    }
    target.innerHTML = `<div class="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-700">${escapeHtml(message)}</div>`;
  }

  function renderCabs(vehicles, trip) {
    const target = document.getElementById("liveCabResults");
    target.innerHTML = `<h3 class="font-bold text-slate-900">Available cabs (${vehicles.length})</h3>${vehicles.map(vehicle => `<article class="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div class="flex flex-wrap items-start justify-between gap-2"><div><h4 class="font-bold text-slate-900">${escapeHtml(vehicle.make_model)}</h4><p class="mt-1 text-xs capitalize text-slate-500">${escapeHtml(vehicle.vehicle_class)} · ${vehicle.seats} seats · ${escapeHtml(vehicle.vendor.business_name)}</p></div><div class="text-right"><div class="font-extrabold text-brand-blue">${money(vehicle.rate_per_km)}/km</div><div class="text-[11px] text-slate-500">+ ${money(vehicle.driver_allowance)} allowance</div></div></div><div class="mt-3 flex flex-wrap gap-2"><button type="button" onclick="viewCabDetails('${vehicle.id}')" class="rounded-lg border border-slate-300 px-3 py-2 text-xs font-bold">Cab details</button><button type="button" onclick='requestCabBooking(${JSON.stringify(vehicle.id)},${JSON.stringify(trip)})' class="rounded-lg bg-brand-blue px-3 py-2 text-xs font-bold text-white hover:bg-brand-bluedark">Request this cab</button></div></article>`).join("")}`;
  }

  async function viewCabDetails(vehicleId) {
    try {
      const vehicle = await request(`/cabs/${vehicleId}`);
      const target = document.getElementById("liveCabResults");
      target.insertAdjacentHTML("afterbegin", `<div class="rounded-xl border border-brand-blue/20 bg-blue-50 p-4 text-sm"><strong>${escapeHtml(vehicle.make_model)}</strong><p class="mt-1 text-slate-700">${escapeHtml(vehicle.vendor.business_name)} · ${escapeHtml(vehicle.vendor.base_city)}</p><p class="mt-1 text-slate-700">${vehicle.seats} seats · ${money(vehicle.rate_per_km)}/km · ${money(vehicle.driver_allowance)} driver allowance</p></div>`);
    } catch (error) { showSearchMessage(error.message); }
  }

  async function requestCabBooking(vehicleId, trip) {
    if (!activeUser || !accessToken) {
      deferredBooking = true;
      showAuthForm("signin");
      showNotice("Sign in or create a customer account to request a cab.", "success");
      return;
    }
    if (activeUser.role !== "customer") {
      showSearchMessage("Sign in with a customer account to request a booking.");
      return;
    }
    try {
      const booking = await request("/bookings", {
        method: "POST",
        body: {
          vehicle_id: vehicleId,
          trip_type: trip.tripType,
          pickup_city: trip.pickup,
          drop_city: trip.drop,
          pickup_at: trip.pickupAt,
          dropoff_at: trip.dropoffAt,
          passengers: 1
        }
      });
      showSearchMessage(`Booking request sent. Reference ${booking.id}. The cab/vendor and dispatch team have been notified. Your booking is not confirmed until you accept the admin's quoted fare.`);
    } catch (error) {
      showSearchMessage(error.message);
    }
  }

  async function confirmCabBooking(id) {
    try { await request(`/bookings/${id}/confirm`, { method: "POST", body: {} }); await renderDashboard(); showNotice("Booking confirmed. We will notify you when a driver is assigned.", "success"); }
    catch (error) { showNotice(error.message); }
  }

  async function cancelCabBooking(id) {
    if (!window.confirm("Cancel this booking?")) return;
    try { await request(`/bookings/${id}/cancel`, { method: "POST", body: {} }); await renderDashboard(); showNotice("Booking cancelled.", "success"); }
    catch (error) { showNotice(error.message); }
  }

  async function readCabNotification(id) {
    try { await request(`/notifications/${id}/read`, { method: "POST", body: {} }); await loadMyNotifications(); }
    catch (error) { showNotice(error.message); }
  }

  async function reviewCabAccount(resource, id, status) {
    try { await request(`/admin/${resource}/${id}/status`, { method: "PATCH", body: { status } }); await renderDashboard(); showNotice(`Account marked ${status}.`, "success"); }
    catch (error) { showNotice(error.message); }
  }

  async function quoteCabBooking(id) {
    const input = document.getElementById(`fare-${id}`);
    const quotedFare = Number(input?.value);
    if (!Number.isSafeInteger(quotedFare) || quotedFare < 1) return showNotice("Enter a valid fare in whole rupees.");
    try { await request(`/admin/bookings/${id}/quote`, { method: "POST", body: { quoted_fare: quotedFare } }); await renderDashboard(); showNotice("Fare sent to the customer for confirmation.", "success"); }
    catch (error) { showNotice(error.message); }
  }

  async function adminBookingAction(id, action) {
    if (action === "reject" && !window.confirm("Reject this booking request?")) return;
    try { await request(`/admin/bookings/${id}/${action}`, { method: "POST", body: {} }); await renderDashboard(); showNotice(`Booking ${action === "complete" ? "completed" : "rejected"}.`, "success"); }
    catch (error) { showNotice(error.message); }
  }

  async function assignCabDriver(bookingId, driverId) {
    try { await request(`/admin/bookings/${bookingId}/assign`, { method: "POST", body: { driver_id: driverId } }); await renderDashboard(); showNotice("Driver assigned and notified.", "success"); }
    catch (error) { showNotice(error.message); }
  }

  async function submitContact(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const notice = document.getElementById("contactNotice");
    const button = form.querySelector("button[type=submit], button:not([type])");
    if (button) { button.disabled = true; button.textContent = "Sending..."; }
    try {
      const result = await request("/contact", { method: "POST", body: Object.fromEntries(new FormData(form).entries()) });
      notice.textContent = `Message received. Reference ${result.id}.`;
      notice.className = "mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800";
      form.reset();
    } catch (error) {
      notice.textContent = error.message;
      notice.className = "mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-800";
    } finally {
      if (button) { button.disabled = false; button.textContent = "Send message"; }
    }
  }

  function openContactPane() {
    ensurePanes();
    const pane = document.getElementById("appContactPane");
    pane.classList.remove("hidden");
    pane.classList.add("flex");
    document.getElementById("contactNotice").classList.add("hidden");
  }

  async function signOutCab() {
    try { await request("/auth/signout", { method: "POST", body: {} }); } catch { /* Local session is cleared even if the service cannot be reached. */ }
    setSession(null, "");
    showAuthForm("signin");
    showNotice("You have signed out.", "success");
  }

  function closeAppPane() {
    document.getElementById("appAccountPane")?.classList.add("hidden");
  }

  function closeContactPane() {
    document.getElementById("appContactPane")?.classList.add("hidden");
  }

  function refreshDashboard() {
    return renderDashboard();
  }

  window.openAccountPane = openAccountPane;
  window.showCabAuth = showAuthForm;
  window.updateSignupFields = updateSignupFields;
  window.closeAppPane = closeAppPane;
  window.openContactPane = openContactPane;
  window.closeContactPane = closeContactPane;
  window.refreshDashboard = refreshDashboard;
  window.loadMyBookings = loadMyBookings;
  window.loadMyNotifications = loadMyNotifications;
  window.signOutCab = signOutCab;
  window.confirmCabBooking = confirmCabBooking;
  window.cancelCabBooking = cancelCabBooking;
  window.readCabNotification = readCabNotification;
  window.reviewCabAccount = reviewCabAccount;
  window.quoteCabBooking = quoteCabBooking;
  window.adminBookingAction = adminBookingAction;
  window.assignCabDriver = assignCabDriver;
  window.viewCabDetails = viewCabDetails;
  window.requestCabBooking = requestCabBooking;

  window.bookViaWhatsApp = async function bookViaWhatsApp() {
    await searchCabs();
  };

  window.submitAgentRegistration = async function submitAgentRegistration() {
    showAuthForm("signup");
    const role = document.getElementById("accountRole");
    if (role) {
      role.value = "vendor";
      updateSignupFields();
      const agency = document.getElementById("agentAgencyName")?.value.trim();
      const contact = document.getElementById("agentContactName")?.value.trim();
      const phone = document.getElementById("agentPhone")?.value.trim();
      const form = document.getElementById("accountForm");
      if (agency) form.elements.business_name.value = agency;
      if (contact) form.elements.name.value = contact;
      if (phone) form.elements.phone.value = phone;
    }
    setStatus("Create your secure vendor account");
  };

  window.submitVendorOnboarding = async function submitVendorOnboarding() {
    showAuthForm("signup");
    const role = document.getElementById("accountRole");
    if (role) {
      role.value = "vendor";
      updateSignupFields();
      const form = document.getElementById("accountForm");
      const name = document.getElementById("vDriverName")?.value.trim();
      const phone = document.getElementById("vDriverPhone")?.value.trim();
      const city = document.getElementById("vDriverCity")?.value.trim();
      if (name) form.elements.name.value = name;
      if (phone) form.elements.phone.value = phone;
      if (city) form.elements.base_city.value = city;
      form.elements.business_name.value = name || "";
      form.elements.partner_type.value = vendorCategory;
      const selectedClass = (document.getElementById("vVehType")?.value || "Sedan").toLowerCase();
      const vehicleClass = selectedClass === "crysta" ? "crysta" : selectedClass;
      const vehicleRates = { hatchback: { perKm: 11, beta: 350 }, sedan: { perKm: 13, beta: 400 }, suv: { perKm: 17, beta: 450 }, crysta: { perKm: 21, beta: 500 } };
      const vehicleRate = vehicleRates[vehicleClass] || vehicleRates.sedan;
      const defaults = { hatchback: 4, sedan: 4, suv: 6, crysta: 7 };
      const seats = defaults[vehicleClass] || 4;
      const explicitSeats = document.getElementById("vVehSeats")?.value;
      pendingVendorVehicle = {
        vehicle_class: vehicleClass,
        make_model: document.getElementById("vVehModel")?.value.trim() || vehicleClass,
        registration_number: document.getElementById("vVehPlate")?.value.trim().toUpperCase() || "",
        seats: explicitSeats ? Number(explicitSeats) : seats,
        rate_per_km: vehicleRate.perKm,
        driver_allowance: vehicleRate.beta
      };
      if (!pendingVendorVehicle.registration_number || !pendingVendorVehicle.make_model) {
        pendingVendorVehicle = null;
        showNotice("Complete the vehicle model and commercial plate fields before continuing.");
        return;
      }
      showNotice("Enter your email and a password of at least 12 characters. Your vendor account and vehicle will be submitted for administrator review.", "success");
    }
  };

  window.addEventListener("DOMContentLoaded", () => {
    ensurePanes();
    try {
      const savedUser = JSON.parse(localStorage.getItem(USER_KEY) || "null");
      const savedToken = localStorage.getItem(TOKEN_KEY) || "";
      if (savedUser && savedToken) setSession(savedUser, savedToken);
    } catch {
      setSession(null, "");
    }
  });
})();

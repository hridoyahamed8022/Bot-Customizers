(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) tg.setHeaderColor("#0c1728");
    if (tg.setBackgroundColor) tg.setBackgroundColor("#09111f");
  }

  const state = {
    movies: [], recent: [], trending: [], upcoming: [], categories: [],
    category: "", query: "", selected: null, detail: null, adUrl: "",
    profileFavorites: [], mayaHistory: []
  };
  let brandName = "Moviex Hub Team";
  const $ = (id) => document.getElementById(id);
  const home = $("home-screen");
  const detailScreen = $("detail-screen");
  const mayaScreen = $("maya-screen");
  const profileScreen = $("profile-screen");
  const toast = $("toast");
  let toastTimer;

  window.setTimeout(() => {
    const splash = $("app-splash");
    if (splash) splash.classList.add("hidden");
  }, 2200);

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[char]);
  }
  function initials(title) {
    return String(title || "M").trim().split(/\s+/).slice(0, 2)
      .map((part) => part[0] || "").join("").toUpperCase();
  }
  function posterMarkup(item, extraClass) {
    const fallback = '<div class="poster-fallback"><span>' + escapeHtml(initials(item.title)) + "</span></div>";
    const image = item.poster_url
      ? '<img src="' + escapeHtml(item.poster_url) + '" alt="' + escapeHtml(item.title) +
        '" loading="lazy" onerror="this.style.display=\'none\'">'
      : "";
    return '<div class="poster-media ' + (extraClass || "") + '">' + image + fallback + "</div>";
  }
  function formatHits(hits) {
    return "◉ " + (hits > 999 ? (hits / 1000).toFixed(1) + "k" : hits || 0);
  }
  function filtered(items) {
    const query = state.query.toLowerCase();
    return items.filter((item) =>
      (!state.category || item.category === state.category) &&
      (!query || item.title.toLowerCase().includes(query)));
  }
  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 3200);
  }
  function applyBrand(name) {
    if (!name) return;
    brandName = name;
    document.title = brandName;
    document.querySelectorAll(".brand-name").forEach((node) => {
      node.textContent = brandName;
    });
    document.querySelector(".topbar-title").textContent = brandName;
  }
  function headers() {
    const result = { "Content-Type": "application/json" };
    if (tg && tg.initData) {
      result["X-Telegram-Init-Data"] = tg.initData;
      result.Authorization = "tma " + tg.initData;
    }
    return result;
  }
  async function api(url, options) {
    if (tg && tg.initData && url.indexOf("init_data=") === -1) {
      url += (url.indexOf("?") === -1 ? "?" : "&") + "init_data=" + encodeURIComponent(tg.initData);
    }
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "আবার চেষ্টা করুন।");
    return data;
  }

  function renderCategories() {
    const all = [{ name: "", count: state.movies.length }].concat(state.categories);
    $("categories").innerHTML = all.map((item, index) => {
      const active = (!state.category && index === 0) || state.category === item.name;
      return '<button type="button" class="' + (active ? "active" : "") + '" data-category="' +
        escapeHtml(item.name) + '">' + escapeHtml(item.name || "All") + "</button>";
    }).join("");
    $("categories").querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        state.category = button.dataset.category || "";
        renderHome();
      });
    });
  }
  function movieCard(movie, compact) {
    return '<button type="button" class="movie-card ' + (compact ? "compact" : "") + '" data-id="' + movie.id + '">' +
      '<div class="poster">' + posterMarkup(movie) +
      '<span class="hits">' + formatHits(movie.hits) + "</span>" +
      (compact ? '<span class="top-badge">🔥 TOP</span>' : "") + "</div>" +
      '<div class="movie-info"><span class="movie-avatar">MB</span><div><h3>' +
      escapeHtml(movie.title) + "</h3><p>" + escapeHtml(movie.category || "মুভি") + "</p></div></div></button>";
  }
  function upcomingCard(item) {
    return '<button type="button" class="movie-card upcoming-card" data-upcoming-id="' + item.id + '">' +
      '<div class="poster">' + posterMarkup(item) +
      '<span class="release-badge">' + escapeHtml(item.release_date || "Coming soon") + "</span></div>" +
      '<div class="movie-info"><span class="movie-avatar">MB</span><div><h3>' +
      escapeHtml(item.title) + "</h3><p>" + escapeHtml(item.category || "Upcoming") + "</p></div></div></button>";
  }
  function bindCards(container) {
    $(container).querySelectorAll(".movie-card").forEach((button) => {
      button.addEventListener("click", () => {
        if (button.dataset.upcomingId) {
          const item = state.upcoming.find((entry) => entry.id === Number(button.dataset.upcomingId));
          if (item) showToast(item.release_date ? item.title + " · " + item.release_date : item.title + " · Coming soon");
          return;
        }
        const movie = state.movies.concat(state.profileFavorites || [])
          .find((entry) => entry.id === Number(button.dataset.id));
        openDetail(movie);
      });
    });
  }
  function renderHome() {
    renderCategories();
    const currentTrending = filtered(state.trending);
    const currentRecent = filtered(state.recent);
    const currentAll = filtered(state.movies);
    const searching = Boolean(state.query || state.category);
    $("trending-section").classList.toggle("hidden", searching);
    $("recent-section").classList.toggle("hidden", searching);
    $("upcoming-section").classList.add("hidden");
    $("all-section").classList.remove("hidden");
    $("all-heading").textContent = searching ? "Search Results" : "All Movies";
    $("trending-strip").innerHTML = currentTrending.map((movie) => movieCard(movie, true)).join("");
    $("recent-grid").innerHTML = currentRecent.map((movie) => movieCard(movie, false)).join("");
    $("movie-grid").innerHTML = currentAll.map((movie) => movieCard(movie, false)).join("");
    $("count").textContent = currentAll.length + "টি";
    $("empty").classList.toggle("hidden", currentAll.length > 0);
    bindCards("trending-strip");
    bindCards("recent-grid");
    bindCards("movie-grid");
  }
  function renderUpcoming() {
    $("trending-section").classList.add("hidden");
    $("recent-section").classList.add("hidden");
    $("all-section").classList.add("hidden");
    $("upcoming-section").classList.remove("hidden");
    $("upcoming-count").textContent = state.upcoming.length + "টি";
    $("upcoming-grid").innerHTML = state.upcoming.map(upcomingCard).join("");
    $("upcoming-empty").classList.toggle("hidden", state.upcoming.length > 0);
    bindCards("upcoming-grid");
    window.scrollTo(0, 0);
  }
  async function loadMovies() {
    try {
      const [movies, upcoming] = await Promise.all([
        api("/api/miniapp/movies?limit=1200", { cache: "no-store" }),
        api("/api/miniapp/upcoming", { cache: "no-store" })
      ]);
      state.movies = movies.movies || [];
      state.recent = movies.recent || [];
      state.trending = movies.trending || [];
      state.categories = movies.categories || [];
      state.upcoming = upcoming.upcoming || [];
      renderHome();
    } catch (error) {
      $("count").textContent = "সমস্যা";
      showToast(error.message || "ডাটা লোড করা যায়নি।");
    }
  }
  async function loadConfig() {
    try {
      const data = await api("/api/miniapp/config", { cache: "no-store" });
      applyBrand(data.brand_name);
    } catch (error) {
      // Static Moviex Hub Team fallback keeps the app usable if Telegram is offline.
    }
  }
  function showModal(id) { $(id).classList.remove("hidden"); }
  function hideModal(id) { $(id).classList.add("hidden"); }
  function setActiveNav(name) {
    document.querySelectorAll(".nav-item").forEach((item) =>
      item.classList.toggle("active", item.dataset.nav === name));
  }
  function showHome() {
    home.classList.remove("hidden");
    detailScreen.classList.add("hidden");
    mayaScreen.classList.add("hidden");
    profileScreen.classList.add("hidden");
    $("back-button").classList.add("hidden");
    document.querySelector(".topbar-title").textContent = brandName;
    renderHome();
    window.scrollTo(0, 0);
  }
  function hideMainScreens() {
    home.classList.add("hidden");
    detailScreen.classList.add("hidden");
    mayaScreen.classList.add("hidden");
    profileScreen.classList.add("hidden");
    $("back-button").classList.add("hidden");
  }
  function renderProfileStats(stats) {
    const items = [
      ["downloads", "Downloads"], ["requests", "Requests"],
      ["favorites", "My List"], ["warnings", "Warnings"]
    ];
    $("profile-stats").innerHTML = items.map((item) =>
      '<div class="profile-stat"><strong>' + (stats[item[0]] || 0) +
      '</strong><span>' + item[1] + "</span></div>").join("");
  }
  async function showProfile() {
    hideMainScreens();
    profileScreen.classList.remove("hidden");
    document.querySelector(".topbar-title").textContent = "Profile";
    $("profile-favorites").innerHTML = '<div class="loading">লোড হচ্ছে...</div>';
    try {
      const data = await api("/api/miniapp/profile", { headers: headers(), cache: "no-store" });
      $("profile-name").textContent = data.profile.name || "Guest";
      $("profile-handle").textContent = data.profile.username ? "@" + data.profile.username : "Moviex Hub Team user";
      $("profile-avatar").textContent = initials(data.profile.name || "G");
      renderProfileStats(data.stats || {});
      state.profileFavorites = data.favorites || [];
      $("profile-favorite-count").textContent = state.profileFavorites.length;
      $("profile-favorites").innerHTML = state.profileFavorites.map((movie) => movieCard(movie, false)).join("");
      $("profile-empty").classList.toggle("hidden", state.profileFavorites.length > 0);
      bindCards("profile-favorites");
    } catch (error) {
      $("profile-favorites").innerHTML = '<div class="empty-state"><p>' +
        escapeHtml(error.message || "Profile লোড করা যায়নি।") + "</p></div>";
    }
    window.scrollTo(0, 0);
  }
  function addMayaMessage(text, role) {
    const node = document.createElement("div");
    node.className = "maya-message " + role;
    node.textContent = text;
    $("maya-messages").appendChild(node);
    $("maya-messages").scrollTop = $("maya-messages").scrollHeight;
  }
  function renderMayaMovies(movies) {
    $("maya-results").innerHTML = (movies || []).map((movie) => movieCard(movie, false)).join("");
    bindCards("maya-results");
  }
  function showMaya() {
    hideMainScreens();
    mayaScreen.classList.remove("hidden");
    document.querySelector(".topbar-title").textContent = "Maya AI";
    window.scrollTo(0, 0);
    $("maya-input").focus();
  }
  async function askMaya(text) {
    addMayaMessage(text, "user");
    $("maya-input").value = "";
    const loading = document.createElement("div");
    loading.className = "maya-message assistant loading";
    loading.textContent = "Maya ভাবছে...";
    $("maya-messages").appendChild(loading);
    try {
      const data = await api("/api/miniapp/maya", {
        method: "POST", headers: headers(),
        body: JSON.stringify({ message: text, history: state.mayaHistory })
      });
      loading.remove();
      addMayaMessage(data.reply || "উত্তর পাওয়া যায়নি।", "assistant");
      renderMayaMovies(data.movies || []);
      state.mayaHistory.push({ role: "user", content: text }, { role: "assistant", content: data.reply || "" });
      state.mayaHistory = state.mayaHistory.slice(-8);
    } catch (error) {
      loading.remove();
      addMayaMessage(error.message || "Maya এখন উত্তর দিতে পারছে না।", "assistant");
    }
  }
  function openDetail(movie) {
    if (!movie) return;
    state.selected = movie;
    state.detail = null;
    home.classList.add("hidden");
    detailScreen.classList.remove("hidden");
    mayaScreen.classList.add("hidden");
    profileScreen.classList.add("hidden");
    $("back-button").classList.remove("hidden");
    document.querySelector(".topbar-title").textContent = brandName;
    $("detail-poster").innerHTML = posterMarkup(movie, "detail-poster-image");
    $("detail-category").textContent = movie.category || "মুভি";
    $("detail-title").textContent = movie.title;
    $("detail-meta").textContent = formatHits(movie.hits) + "  ·  " + (movie.file_type || "Video").toUpperCase();
    $("detail-caption").textContent = movie.caption || "মুভিটি পেতে Download বাটনে চাপুন।";
    $("favorite-button").innerHTML = "♡ <span>Like</span>";
    renderStars(0);
    renderComments([]);
    api("/api/miniapp/movies/" + movie.id, { headers: headers(), cache: "no-store" })
      .then((data) => {
        state.detail = data;
        $("favorite-button").innerHTML = (data.favorite ? "♥" : "♡") + " <span>Like</span>";
        renderStars(data.user_rating || 0, data.rating);
      }).catch(() => {});
    api("/api/miniapp/movies/" + movie.id + "/comments", { cache: "no-store" })
      .then((data) => renderComments(data.comments || [])).catch(() => {});
    window.scrollTo(0, 0);
  }
  function renderStars(selected, rating) {
    $("rating-stars").innerHTML = [1, 2, 3, 4, 5].map((number) =>
      '<button type="button" data-rating="' + number + '" class="' + (number <= selected ? "selected" : "") + '">★</button>'
    ).join("") + (rating && rating.count ? '<small>' + rating.average + " / 5 (" + rating.count + ")</small>" : "");
    $("rating-stars").querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => rateMovie(Number(button.dataset.rating)));
    });
  }
  function renderComments(comments) {
    $("comments-heading").textContent = "Comments (" + comments.length + ")";
    $("comments-list").innerHTML = comments.length ? comments.map((item) =>
      '<div class="comment-item"><strong>' + escapeHtml(item.name) + '</strong><p>' +
      escapeHtml(item.comment) + "</p></div>").join("") :
      '<p class="no-comments">এখনও কোনো comment নেই। প্রথম comment করুন!</p>';
  }
  async function rateMovie(rating) {
    try {
      const data = await api("/api/miniapp/rating", {
        method: "POST", headers: headers(),
        body: JSON.stringify({ movie_id: state.selected.id, rating })
      });
      renderStars(rating, { average: data.average, count: data.count });
      showToast("আপনার rating সংরক্ষণ হয়েছে।");
    } catch (error) { showToast(error.message); }
  }
  async function addComment() {
    const input = $("comment-input");
    const comment = input.value.trim();
    if (!comment || !state.selected) return;
    $("comment-button").disabled = true;
    try {
      await api("/api/miniapp/movies/" + state.selected.id + "/comments", {
        method: "POST", headers: headers(), body: JSON.stringify({ movie_id: state.selected.id, comment })
      });
      input.value = "";
      const data = await api("/api/miniapp/movies/" + state.selected.id + "/comments", { cache: "no-store" });
      renderComments(data.comments || []);
      showToast("Comment যোগ হয়েছে।");
    } catch (error) { showToast(error.message); }
    finally { $("comment-button").disabled = false; }
  }
  $("favorite-button").addEventListener("click", async () => {
    if (!state.selected) return;
    if (!tg || !tg.initData) return showToast("বটের ভেতর থেকে Like দিন।");
    try {
      const data = await api("/api/miniapp/favorite", {
        method: "POST", headers: headers(), body: JSON.stringify({ movie_id: state.selected.id })
      });
      $("favorite-button").innerHTML = (data.favorite ? "♥" : "♡") + " <span>Like</span>";
      showToast(data.favorite ? "My List-এ যোগ হয়েছে।" : "My List থেকে সরানো হয়েছে।");
    } catch (error) { showToast(error.message); }
  });
  $("download-button").addEventListener("click", () => {
    if (!state.selected) return;
    const match = (state.selected.file_name || "").match(/(2160|1440|1080|720|480)p/i);
    $("quality-button").querySelector("span").textContent = "⇩ Download " + (match ? match[1] : "720") + "p";
    showModal("quality-modal");
  });
  $("quality-button").addEventListener("click", async () => {
    if (!state.selected) return;
    if (!tg || !tg.initData) return showToast("বটের ভেতর থেকে Download করুন।");
    $("quality-button").disabled = true;
    $("quality-button").querySelector("span").textContent = "লিংক তৈরি হচ্ছে...";
    try {
      const data = await api("/api/miniapp/claim", {
        method: "POST", headers: headers(), body: JSON.stringify({ movie_id: state.selected.id })
      });
      state.adUrl = data.ad_url;
      hideModal("quality-modal");
      showModal("unlock-modal");
    } catch (error) { showToast(error.message); }
    finally { $("quality-button").disabled = false; }
  });
  $("open-ad-button").addEventListener("click", () => {
    if (!state.adUrl) return showToast("লিংক তৈরি হয়নি।");
    hideModal("unlock-modal");
    showToast("Sending File... ১০ সেকেন্ড পরে bot inbox দেখুন।");
    if (tg && tg.openLink) tg.openLink(state.adUrl);
    else window.location.href = state.adUrl;
  });
  $("comment-button").addEventListener("click", addComment);
  $("comment-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); addComment(); }
  });
  $("back-button").addEventListener("click", () => {
    showHome();
    setActiveNav("home");
  });
  $("search").addEventListener("input", (event) => {
    state.query = event.target.value.trim();
    renderHome();
  });
  $("close-announcement").addEventListener("click", () => $("announcement").remove());
  $("trending-more").addEventListener("click", () => { state.query = ""; state.category = ""; renderHome(); $("all-section").scrollIntoView({ behavior: "smooth" }); });
  $("recent-more").addEventListener("click", () => { state.query = ""; state.category = ""; renderHome(); $("all-section").scrollIntoView({ behavior: "smooth" }); });
  $("maya-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = $("maya-input").value.trim();
    if (value) askMaya(value);
  });
  document.querySelectorAll("[data-maya-prompt]").forEach((button) => {
    button.addEventListener("click", () => askMaya(button.dataset.mayaPrompt));
  });
  document.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", () => hideModal(button.dataset.closeModal));
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      const nav = button.dataset.nav;
      if (nav === "home") showHome();
      else if (nav === "search") { showHome(); $("search").focus(); window.scrollTo(0, 0); }
      else if (nav === "upcoming") {
        hideMainScreens(); home.classList.remove("hidden");
        $("back-button").classList.add("hidden"); renderUpcoming();
      } else if (nav === "maya") showMaya();
      else if (nav === "profile") showProfile();
    });
  });
  loadConfig();
  loadMovies();
})();
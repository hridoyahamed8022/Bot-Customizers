(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) tg.setHeaderColor("#0b1020");
    if (tg.setBackgroundColor) tg.setBackgroundColor("#080d1b");
  }

  const state = { movies: [], category: "", query: "", selected: null };
  const grid = document.getElementById("movie-grid");
  const categories = document.getElementById("categories");
  const count = document.getElementById("count");
  const empty = document.getElementById("empty");
  const modal = document.getElementById("detail-modal");
  const toast = document.getElementById("toast");
  let toastTimer;

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, function (char) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[char];
    });
  }

  function initials(title) {
    return String(title || "M").trim().split(/\s+/).slice(0, 2).map(function (part) {
      return part[0] || "";
    }).join("").toUpperCase();
  }

  function posterMarkup(movie, detail) {
    const fallback = '<div class="poster-fallback"><span>' + escapeHtml(initials(movie.title)) + "</span></div>";
    if (!movie.poster_url) return fallback;
    const image = '<img src="' + escapeHtml(movie.poster_url) + '" alt="' + escapeHtml(movie.title) + '" loading="lazy">';
    return detail ? image : image;
  }

  function filteredMovies() {
    const query = state.query.toLowerCase();
    return state.movies.filter(function (movie) {
      const matchesCategory = !state.category || movie.category === state.category;
      const matchesQuery = !query || movie.title.toLowerCase().includes(query);
      return matchesCategory && matchesQuery;
    });
  }

  function renderCategories(items) {
    const all = [{ name: "", count: state.movies.length }].concat(items || []);
    categories.innerHTML = all.map(function (item, index) {
      const label = item.name || "সব";
      return '<button type="button" class="' + ((!state.category && index === 0) || state.category === item.name ? "active" : "") + '" data-category="' + escapeHtml(item.name) + '">' +
        escapeHtml(label) + ' <small>(' + item.count + ")</small></button>";
    }).join("");
    categories.querySelectorAll("button").forEach(function (button) {
      button.addEventListener("click", function () {
        state.category = button.dataset.category || "";
        renderCategories(items);
        renderMovies();
      });
    });
  }

  function renderMovies() {
    const movies = filteredMovies();
    count.textContent = movies.length + "টি";
    empty.classList.toggle("hidden", movies.length > 0);
    grid.innerHTML = movies.map(function (movie) {
      return '<button type="button" class="movie-card" data-id="' + movie.id + '">' +
        '<div class="poster">' + posterMarkup(movie, false) + "</div>" +
        '<div class="movie-info"><h3>' + escapeHtml(movie.title) + "</h3>" +
        '<p>' + escapeHtml(movie.category || "মুভি") + "</p></div></button>";
    }).join("");
    grid.querySelectorAll(".movie-card").forEach(function (card) {
      card.addEventListener("click", function () {
        openDetail(state.movies.find(function (movie) { return movie.id === Number(card.dataset.id); }));
      });
    });
  }

  function openDetail(movie) {
    if (!movie) return;
    state.selected = movie;
    document.getElementById("detail-poster").innerHTML = posterMarkup(movie, true);
    document.getElementById("detail-category").textContent = movie.category || "মুভি";
    document.getElementById("detail-title").textContent = movie.title;
    document.getElementById("detail-caption").textContent = movie.caption || "এই মুভির ফাইল পেতে নিচের বাটনে চাপুন।";
    document.getElementById("claim-button").disabled = false;
    document.getElementById("claim-button").textContent = "১০ সেকেন্ডের ad link নিন";
    document.getElementById("claim-note").textContent = "সময় শেষ হলে আপনাকে স্বয়ংক্রিয়ভাবে বটে ফিরিয়ে দেওয়া হবে।";
    modal.classList.remove("hidden");
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove("show"); }, 3200);
  }

  async function loadMovies() {
    try {
      const response = await fetch("/api/miniapp/movies", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error("মুভি লোড করা যায়নি।");
      state.movies = data.movies || [];
      renderCategories(data.categories);
      renderMovies();
    } catch (error) {
      count.textContent = "সমস্যা";
      showToast(error.message || "মুভি লোড করা যায়নি।");
    }
  }

  document.getElementById("search").addEventListener("input", function (event) {
    state.query = event.target.value.trim();
    renderMovies();
  });

  document.querySelectorAll("[data-close]").forEach(function (element) {
    element.addEventListener("click", function () { modal.classList.add("hidden"); });
  });

  document.getElementById("claim-button").addEventListener("click", async function () {
    const button = document.getElementById("claim-button");
    if (!state.selected) return;
    if (!tg || !tg.initData) {
      showToast("বটের ভেতর থেকে Mini App খুলে আবার চেষ্টা করুন।");
      return;
    }
    button.disabled = true;
    button.textContent = "লিংক তৈরি হচ্ছে...";
    try {
      const response = await fetch("/api/miniapp/claim", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Telegram-Init-Data": tg.initData
        },
        body: JSON.stringify({ movie_id: state.selected.id, category: state.selected.category })
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Ad link তৈরি করা যায়নি।");
      if (tg.openLink) tg.openLink(data.ad_url);
      else window.location.href = data.ad_url;
      button.textContent = "Ad link খোলা হয়েছে";
    } catch (error) {
      button.disabled = false;
      button.textContent = "১০ সেকেন্ডের ad link নিন";
      showToast(error.message || "আবার চেষ্টা করুন।");
    }
  });

  loadMovies();
})();
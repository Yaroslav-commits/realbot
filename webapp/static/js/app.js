
let tg = window.Telegram.WebApp;
tg.expand();

let allCards = [];
let userId = tg.initDataUnsafe?.user?.id || 123456789; // Тестовый ID если нет WebApp

function switchTab(tabId, el) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById(tabId).classList.add('active');
    el.classList.add('active');

    if(tabId === 'profile') loadProfile();
}

async function loadCards() {
    const res = await fetch('/api/cards');
    allCards = await res.json();
    renderCards(allCards);
}

async function loadProfile() {
    const res = await fetch(`/api/profile/${userId}`);
    const data = await res.json();
    if(data.error) return;

    document.getElementById('prof-name').innerText = data.nickname || data.username;
    document.getElementById('prof-rank').innerText = data.rank;
    document.getElementById('val-dia').innerText = data.diamond;
    document.getElementById('val-krw').innerText = data.krw;
    document.getElementById('val-bc').innerText = data.battlecoin;
    document.getElementById('stat-wins').innerText = data.wins;
}

function renderCards(cards) {
    const container = document.getElementById('cardsContainer');
    container.innerHTML = cards.map(card => `
        <div class="card" data-rarity="${card.rarity.split(' ')[0]}">
            <img src="/cards/${card.file}" onerror="this.src='https://placehold.co/150x200/111118/555577?text=No+Image'">
            <h3>${card.name}</h3>
            <div class="rarity rarity-${card.rarity.replace(/\s+/g, '')}">${card.rarity}</div>
            <div class="stats">
                <span>💪 <b>${card.strength}</b></span>
                <span>⚡️ <b>${card.speed}</b></span>
                <span>🧠 <b>${card.intellect}</b></span>
            </div>
        </div>
    `).join('');
}

function updateCards() {
    const search = document.getElementById('searchInput').value.toLowerCase();
    const rarity = document.getElementById('rarityFilter').value;
    const filtered = allCards.filter(c => 
        c.name.toLowerCase().includes(search) && (rarity === 'all' || c.rarity.includes(rarity))
    );
    renderCards(filtered);
}

document.addEventListener('DOMContentLoaded', loadCards);

const tg = window.Telegram.WebApp;
tg.expand();

// Настройки UI под тёмно-синий стиль
tg.setHeaderColor('#020617');
tg.setBackgroundColor('#020617');

// URL твоего API (Оставь пустым, так как HTML и API лежат на одном сервере)
const API_BASE = ''; 
const initData = tg.initData;

function navigate(screenId) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
    
    document.getElementById(`screen-${screenId}`).classList.add('active');
    document.querySelector(`.nav-item[data-target="${screenId}"]`).classList.add('active');
    
    if (screenId !== 'home') {
        tg.BackButton.show();
        tg.BackButton.onClick(() => navigate('home'));
    } else {
        tg.BackButton.hide();
    }
    
    tg.HapticFeedback.impactOccurred('light');
    if (screenId === 'collection') fetchCollection();
}
// Связь с твоим api.py -> @app.get("/api/profile")
async function fetchProfile() {
    try {
        const response = await fetch(`${API_BASE}/api/profile`, {
            headers: { 'X-Init-Data': initData }
        });
        if (!response.ok) return;
        
        const data = await response.json();
        
        document.getElementById('balance-krw').textContent = data.krw;
        document.getElementById('balance-diamonds').textContent = data.diamond;
        document.getElementById('balance-bc').textContent = data.battlecoin;
        
        document.getElementById('header-name').textContent = data.nickname || data.username || 'Игрок';
        document.getElementById('profile-name').textContent = data.nickname || data.username || 'Игрок';
        document.getElementById('profile-title').textContent = data.active_title || 'Нет титула';
        document.getElementById('profile-rank').textContent = `Рейтинг: ${data.rank_points}`;
        document.getElementById('profile-attempts').textContent = data.attempts;
    } catch (e) {
        console.error("Ошибка загрузки профиля:", e);
    }
}

// Связь с твоим api.py -> @app.get("/api/collection")
async function fetchCollection() {
    try {
        const response = await fetch(`${API_BASE}/api/collection`, {
            headers: { 'X-Init-Data': initData }
        });
        if (!response.ok) return;
        
        const data = await response.json();
        document.getElementById('collection-count').textContent = `${data.total_cards} карт`;
        
        const grid = document.getElementById('collection-grid');
        grid.innerHTML = ''; 
        
        if (data.cards.length === 0) {
            grid.innerHTML = '<div class="text-center text-gray-500 col-span-2 py-10">У тебя пока нет карт</div>';
            return;
        }

        data.cards.forEach(card => {
            const cardEl = document.createElement('div');
            let borderColor = 'border-slate-700';
            if (card.rarity === 'Mythic') borderColor = 'border-purple-500 neon-border';
            if (card.rarity === 'Legendary') borderColor = 'border-yellow-500';
            
            cardEl.className = `glass rounded-xl p-2 flex flex-col items-center ${borderColor}`;
            cardEl.innerHTML = `
                <div class="w-full aspect-[3/4] bg-slate-800 rounded-lg mb-2 flex items-center justify-center overflow-hidden relative">
                    <span class="text-slate-600 text-xs">Арт</span>
                </div>
                <span class="font-bold text-sm text-center truncate w-full">${card.name || card.id}</span>
                <span class="text-[10px] text-sky-400 uppercase tracking-wider mt-1">${card.rarity || 'Common'}</span>
            `;
            grid.appendChild(cardEl);
        });
    } catch (e) {
        console.error("Ошибка загрузки коллекции:", e);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    fetchProfile();
});

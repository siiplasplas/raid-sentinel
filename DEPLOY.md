# Sunucuya kurulum

Raid Sentinel'i kendi sunucunda çalıştırmak için. İki yol var: Docker veya
doğrudan systemd. İkisi de aynı sonucu verir, Docker daha az uğraş.

> **Ev bilgisayarında çalıştırma.** PC kapalıyken — yani tam offline raid
> anında — sistem de ölü olur. Küçük bir VPS yeter: 1 vCPU / 1 GB RAM fazlasıyla.

---

## Yol A — Docker (önerilen)

```bash
git clone <repo> raid-sentinel && cd raid-sentinel
cp .env.example .env
mkdir -p data
docker compose up -d --build
```

Panel `127.0.0.1:8787` üzerinde açılır. Sunucudan erişmek için kendi
makinenden SSH tüneli kur:

```bash
ssh -L 8787:localhost:8787 kullanici@sunucu
```

Sonra tarayıcında `http://localhost:8787/` aç.

Logları izlemek için:

```bash
docker compose logs -f
```

## Yol B — systemd (Docker'sız)

```bash
sudo useradd --system --create-home --home-dir /opt/raid-sentinel sentinel
sudo -u sentinel git clone <repo> /opt/raid-sentinel
cd /opt/raid-sentinel
sudo -u sentinel python3.12 -m venv .venv
sudo -u sentinel .venv/bin/pip install .
sudo -u sentinel mkdir -p data
sudo -u sentinel cp .env.example .env

sudo cp deploy/raid-sentinel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now raid-sentinel
```

Durum ve log:

```bash
systemctl status raid-sentinel
journalctl -u raid-sentinel -f
```

---

## Eşleştirme — headless sunucuda

Eşleştirme akışı bir tarayıcı ve `localhost:3000` üzerinde bir geri dönüş
sunucusu gerektiriyor. Sunucuda tarayıcı yok, o yüzden iki seçenek var.

### Seçenek 1 — Kendi bilgisayarında eşleştir, dosyayı kopyala *(en kolay)*

Kendi makinende (tarayıcı var):

```bash
pip install .
sentinel pair
```

Steam girişini tamamla, sonra oluşan dosyayı sunucuya kopyala:

```bash
scp data/rustplus.config.json kullanici@sunucu:/opt/raid-sentinel/data/
```

Docker kullanıyorsan hedef `./data/rustplus.config.json`. Sonra servisi
yeniden başlat.

### Seçenek 2 — SSH tüneliyle sunucuda eşleştir

Kendi makinenden tünel aç:

```bash
ssh -L 3000:localhost:3000 kullanici@sunucu
```

Aynı SSH oturumunda:

```bash
cd /opt/raid-sentinel && .venv/bin/sentinel pair
```

Komut bir adres yazdırır ama sunucuda tarayıcı açamaz. Kendi tarayıcında
`http://localhost:3000/` aç, Steam girişini orada tamamla. Tünel token'ı
sunucuya taşır.

Docker'da bu yol daha karışık — tünel ana makinede biter, kapta değil. Kapta
eşleştirmek istersen geri dönüş sunucusunu dışarı aç:

```bash
docker compose run --rm -p 127.0.0.1:3000:3000 sentinel \
  sentinel pair --host 0.0.0.0
```

**Seçenek 1 daha az uğraştırır.** Eşleştirme bir kereye mahsus (wipe'a kadar).

---

## Kurulum sonrası

1. Panele bağlan (SSH tüneli) → **Kurulum** sekmesi kontrol listesini gösterir.
2. **Ayarlar** sekmesinden bildirim kanalını gir, "Bildirimleri dene" ile test et.
3. Oyunda `ESC → Rust+ → Pair with Server`, sonra alarmlara `Pair`.
4. **Üs** sekmesinden bölgeleri çiz.
5. **Cihazlar** sekmesinde "Test tetikle" ile tüm zinciri doğrula.

Ayrıntılı test senaryoları için [TESTING.md](TESTING.md).

---

## Yedekleme

Kaybedilmemesi gereken tek dizin `data/`:

| Dosya | İçerik | Kaybedilirse |
|---|---|---|
| `rustplus.config.json` | FCM kimlikleri, eşleşmiş sunucu | Yeniden eşleştirme |
| `sentinel.db` | Olay geçmişi, cihaz kayıtları | Geçmiş ve bölge atamaları gider |
| `settings.json` | Panelden yapılan ayarlar | Ayarları yeniden gir |
| `base.json` | Üs tanımı | ETA çalışmaz, yeniden çiz |

```bash
tar czf sentinel-backup-$(date +%F).tar.gz data/
```

`rustplus.config.json` içinde Steam oturumundan türetilmiş token var —
yedeği paylaşma.

---

## Dış izleme (dead-man switch)

`/health` ucu sistem sağlıksızsa **503** döner. Bir uptime servisine
(Uptime Kuma, healthchecks.io, UptimeRobot) bağlarsan program sessizce
öldüğünde haberin olur. Bu, alarm sistemlerinin en sık ölüm şekli.

Panel yalnızca yerele bağlı olduğu için dış servis doğrudan erişemez. İki yol:

- Uptime Kuma gibi bir aracı aynı sunucuda çalıştır, `http://127.0.0.1:8787/health` adresini izlet.
- Ya da ters vekilde sadece `/health` yolunu dışarı aç (kimlik doğrulamalı).

## Güncelleme

```bash
git pull
docker compose up -d --build          # Docker
# veya
sudo -u sentinel .venv/bin/pip install --upgrade .   # systemd
sudo systemctl restart raid-sentinel
```

`data/` dokunulmaz. Veritabanı şeması gerekiyorsa açılışta kendini günceller.

---

## Güvenlik notları

- Panelde **oturum yönetimi yok.** Varsayılan olarak yalnızca `127.0.0.1`'e
  bağlanır ve öyle kalmalı. Erişim için SSH tüneli kullan.
- Dışarı açmak zorundaysan önüne ters vekil koy ve HTTP temel kimlik
  doğrulaması ekle. Panelden Twilio token'ı ve webhook adresleri
  değiştirilebiliyor — açık bırakılan bir panel bunları ele geçirir.
- `data/` dizini `700`, içindeki dosyalar servis kullanıcısına ait olmalı.
- Docker imajı kök olmayan kullanıcıyla çalışıyor (`uid 10001`).

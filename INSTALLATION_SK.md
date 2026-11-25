# Inštalácia Midea LAN v1.0.4 na Homey Pro

## Oprava Homey Energy

Vo verzii 1.0.2 režim **Automaticky** vždy uprednostnil natívne kumulatívne počítadlo klimatizácie. Pri niektorých Midea modeloch je toto LAN počítadlo pomalé, má nízke rozlíšenie alebo sa dekóduje inak než denný report v SmartHome. Výsledkom mohla byť napríklad nižšia spotreba v Homey než v SmartHome.

Verzia 1.0.4 používa nový hybridný tracker:

```text
bežná prevádzka: aktuálny príkon → integrácia do kWh
výpadok alebo chýbajúci príkon: natívne počítadlá → obnova rozdielu
bez merania: nakonfigurovaný odhad
```

Zároveň je opravená interná kolízia názvu `_energy`. Tento názov používa aj samotný Homey Python SDK; aplikácia teraz používa vlastné pole `_energy_tracker`.

Existujúca hodnota `meter_power` sa pri aktualizácii zachová a nikdy sa nezníži. Historický údaj, ktorý už Homey zapísal do minulého dňa, sa spätne neprepisuje. Oprava sa prejaví na spotrebe od spustenia v1.0.4.

## Ďalšie zmeny

- aktuálny príkon sa pri štarte aplikácie už nenuluje skôr, než príde nový stav,
- tracker sa ukladá približne každú minútu namiesto každých päť minút,
- natívne celkové aj resetovateľné počítadlo sa sledujú samostatne,
- dlhší výpadok sa obnoví z rozdielu natívneho počítadla,
- trvalých `0 W` počas zapnutej klimatizácie sa považuje za nepodporované live meranie,
- pridaná je kalibrácia aktuálneho príkonu, predvolene 100 %,
- nová automatická voľba dekódovania používa metódu 12 pre PortaSplit a 101 pre model `00000Q1D` subtype 524,
- knižnica je aktualizovaná na `midea-lan==2026.9.0`.

## Aktualizácia na MacBooku

1. Ukonči aktuálne testovacie spustenie:

```text
Control + C
```

2. Premenuj starý priečinok:

```bash
mv ~/Downloads/com.tothk.midea ~/Downloads/com.tothk.midea-v1.0.2-old
```

3. Rozbaľ nový ZIP. Výsledný priečinok musí byť:

```text
~/Downloads/com.tothk.midea
```

4. Keďže sa mení verzia knižnice `midea-lan`, priprav závislosti znova:

```bash
cd ~/Downloads/com.tothk.midea
homey app dependencies install
homey app validate
homey app run
```

Docker Desktop musí byť spustený.

## Odporúčané nastavenia

V zariadení Midea otvor **Settings** a nastav:

```text
Zdroj výpočtu spotreby: Automaticky
Metóda dekódovania spotreby: Automaticky
Kalibrácia aktuálneho príkonu: 100 %
```

V režime **Automaticky** sa v položke **Zdroj merania energie** počas bežnej prevádzky očakáva:

```text
Integrácia aktuálneho príkonu
```

Pri obnove dlhšieho výpadku sa môže dočasne zobraziť:

```text
Meranie klimatizácie
```

Ak sa aktuálny príkon vo W zjavne nezhoduje s realitou, vyskúšaj inú **Metódu dekódovania spotreby**. Dostupné sú `1`, `2`, `3`, `12` a `101`. Kalibráciu percentami používaj až po porovnaní celého dňa, nie podľa jedného okamžitého odberu.

## Trvalá inštalácia

Po úspešnom teste:

```bash
homey app install
```


## Bezpečný LAN režim (v1.0.4)

Je predvolene zapnutý a platí pri vypnutej aj zapnutej klíme. Appka neposiela periodické status/capability query bursty do klimatizácie. Ponechá iba základný handshake, šifrované TCP spojenie, heartbeat, ovládacie príkazy a aktívne hlásenia zariadenia. Tým sa obchádza známe správanie 35/88, náhodné pípanie, rozsvecovanie displeja a možné resetovanie Wi-Fi modulu. Energy tracker sa aktualizuje lokálne každú minútu bez sieťového dotazu na klímu. Bezpečný režim vypínajte iba pri diagnostike, ak viete, že váš model aktívny polling znáša.

# ThirdEyeInsight

**Analyseur de decks Yu-Gi-Oh! — probabilités, cohérence et simulation de main d'ouverture.**

## Pourquoi cet outil ?

La plupart des joueurs construisent leur deck "au feeling" : ils copient une liste vue en ligne, ajustent quelques cartes selon leur intuition et espèrent que ça marche. Le problème, c'est que l'intuition se trompe souvent sur des questions pourtant très concrètes : *"si je passe de 2 à 3 exemplaires de cette carte, à quel point ça change vraiment mes chances de main jouable ?"*, *"est-ce que mon deck dépend d'une poignée de cartes précises, ou l'ouverture est-elle bien répartie ?"*, *"dois-je vraiment sider cette carte contre ce matchup, ou est-ce que je perds plus que je ne gagne ?"* — ce sont des questions de probabilités, pas de ressenti et le cerveau humain est notoirement mauvais pour estimer des probabilités à l'instinct.

ThirdEyeInsight remplace l'intuition par le calcul : au lieu de deviner, il simule des centaines de milliers de mains d'ouverture et donne des chiffres exacts. Le but n'est pas de dire "ce deck est bon ou mauvais", c'est de donner aux joueurs les moyens de comprendre **pourquoi** un ratio de cartes fonctionne ou pas, de comparer objectivement deux versions d'un side deck avant un tournoi et de repérer les points faibles d'une liste (trop de bricks, un starter trop peu redondant, une main morte trop fréquente) avant de les découvrir en partie.

## Comprendre les rôles de carte

Le cœur de l'outil repose sur un principe simple : chaque carte du deck se voit assigner un ou plusieurs **rôles**, qui décrivent sa fonction dans la stratégie du deck plutôt que son texte brut. C'est cette classification qui permet ensuite tous les calculs de probabilité par catégorie.

| Rôle | Ce que ça représente |
|---|---|
| **Starter** | Une carte capable de lancer ton combo/ta stratégie à elle seule (celle que tu as besoin de piocher pour "faire ton tour") |
| **Extender** | Une carte qui prolonge un combo déjà lancé par un Starter, mais qui ne démarre (presque) rien toute seule |
| **Handtrap** | Une carte jouable depuis la main pendant le tour adverse, pour perturber son combo |
| **Anti-Handtrap** | Une carte qui protège contre les handtraps adverses ("Appelé par la Tombe", 'Désignateur de la Suppression", etc.) |
| **Boardbreaker** | Une carte utilisée en Second pour percer le board/backrow adverse ("Raigeki", 'Plumeau de Dame Harpie", etc.)|
| **Brick** | Une carte "morte" en main si elle est isolée — inutile sans le reste de sa combo, ou situationnelle |
| **Pioche** | Une carte qui fait piocher des cartes supplémentaires si elle est en main d'ouverture (ex. Pot of Desires, Upstart Goblin) |

Une carte peut cumuler plusieurs rôles à la fois (une carte peut très bien être Starter ET Handtrap si son texte le permet) — rien n'oblige à n'en choisir qu'un seul.

### Comment classer une carte dans l'application

Dans la page **Construction du Deck**, recherche une carte puis, dans le panneau "Ajout / Édition de carte" qui apparaît, coche la ou les cases correspondant à son rôle avant de l'ajouter au deck. Pour une carte de rôle **Pioche**, un champ supplémentaire permet d'indiquer combien de cartes elle fait piocher.

Cette classification n'a besoin d'être faite qu'une fois par carte : elle est ensuite utilisée automatiquement dans tous les calculs de probabilité, la simulation de main d'ouverture, la détection de main morte, et l'analyse de concentration.

## Fonctionnalités

- **Construction de deck** — recherche de cartes via l'API YGOPRODeck, gestion Main/Side Deck, filtres par rôle (Starter, Extender, Handtrap, Anti-Handtrap, Boardbreaker, Brick), import/export au format `.ydk`, import direct depuis une URL ou un lien `ydke://`
- **Analyse de probabilité** — calcul par loi hypergéométrique exacte (pas seulement une simulation) pour chaque rôle, avec la redondance (exactement 1 vs exactement 2 exemplaires) affichée séparément
- **Simulation Monte-Carlo** — des centaines de milliers de mains tirées pour évaluer le taux de main jouable, en Premier comme en Second, avec ou sans les combos personnalisés
- **Combos personnalisés** — définis comme des paires de cartes, avec une portée par scénario ou globale
- **Conditions de victoire personnalisées** — au-delà du calcul standard, définis tes propres règles ("au moins 1 Starter ET au moins 2 Handtraps", etc.) avec des opérateurs ET/OU combinables
- **Scénarios post-side** — simule un side deck nommé (ex. "Face à un deck de contrôle") en échangeant des cartes entre Main et Side, sans jamais toucher au deck sauvegardé
- **Comparaison de scénarios** — deux configurations côte à côte, pour trancher entre deux side decks possibles
- **Cartes Pioche** — prend en compte les cartes qui piochent en main d'ouverture (Pot of Greed & co.) dans les calculs
- **Concentration des starters** — identifie si le deck dépend de quelques cartes précises ou si l'ouverture est bien répartie entre plusieurs
- **6 langues** — français, anglais, espagnol, allemand, italien, portugais
- **100% hors ligne après le premier lancement** — la base de cartes et les images sont mises en cache localement

## Aperçu

<table>
  <tr>
    <td align="center" width="50%">
      <img width="1117" height="686" alt="image" src="https://github.com/user-attachments/assets/5edee46f-3af2-402d-8bcf-f9d4ba5e3a1b" /><br/>
      <sub><b>Construction du Deck</b></sub>
    </td>
    <td align="center" width="50%">
      <img width="1120" height="685" alt="image" src="https://github.com/user-attachments/assets/9d99396a-58dd-4cf1-a0b5-1d3be1fdff8d" /><br/>
      <sub><b>Combos Starters</b></sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img width="1116" height="680" alt="image" src="https://github.com/user-attachments/assets/093fcd83-7559-4eb6-bdc8-1f3ae1725735" /><br/>
      <sub><b>Scénarios</b></sub>
    </td>
    <td align="center" width="50%">
      <img width="1121" height="673" alt="image" src="https://github.com/user-attachments/assets/2fc5d906-33fc-474c-8b4f-29f40149614c" /><br/>
      <sub><b>Analyse</b></sub>
    </td>
  </tr>
</table>

## Installation

### Télécharger l'exécutable (le plus simple)

Pas besoin d'installer Python : télécharge directement le `.exe` depuis la page [**Releases**](../../releases) du dépôt, dans un dossier dédié, et lance-le.

> Place l'exécutable dans son propre dossier avant de le lancer pour la première fois — l'application y créera automatiquement `decks/`, `exports/`, `imports/` et `.cache/` juste à côté. Aucune installation requise.

### Depuis les sources (pour développer)

#### Prérequis

- Python 3.10 ou supérieur
- pip

```bash
git clone https://github.com/<ton-compte>/ThirdEyeInsight.git
cd ThirdEyeInsight

python3 -m venv env
source env/bin/activate          # Windows : env\Scripts\activate.bat

pip install -r requirements.txt
python main.py
```

Au premier lancement, l'application télécharge et met en cache la base de cartes Yu-Gi-Oh! (via l'API [YGOPRODeck](https://ygoprodeck.com/)) ainsi que les images des cartes utilisées — une connexion internet est donc nécessaire la première fois. Les lancements suivants fonctionnent hors ligne.

## Utilisation

L'application s'articule autour de 4 pages :

1. **Construction du Deck** — ajouter/retirer des cartes, leur assigner un rôle
2. **Combos Starters** — définir des paires de cartes formant un combo d'ouverture
3. **Scénarios** — construire des variantes post-side du deck actif
4. **Analyse** — lancer les calculs de probabilité, comparer des scénarios, ajuster les conditions de victoire

Tous les decks, combos, conditions et scénarios sont sauvegardés localement dans le dossier `decks/`, organisés par deck. Les exports `.ydk` atterrissent dans `exports/`.

## Compiler un exécutable autonome

L'exécutable est généré via [PyInstaller](https://pyinstaller.org/).

### Sur Linux (Debian et dérivés)

```bash
source env/bin/activate
pip install pyinstaller

pyinstaller --onefile --windowed --name ThirdEyeInsight \
  --add-data "$(python3 -c 'import customtkinter, os; print(os.path.dirname(customtkinter.__file__))'):customtkinter/" \
  --hidden-import="PIL._tkinter_finder" \
  main.py
```

L'exécutable final se trouve dans `dist/ThirdEyeInsight`.

### Sur Windows (cmd)

```bat
env\Scripts\activate.bat
pip install pyinstaller

for /f "delims=" %i in ('python -c "import customtkinter, os; print(os.path.dirname(customtkinter.__file__))"') do set CTK_PATH=%i

pyinstaller --onefile --windowed --name ThirdEyeInsight ^
  --add-data "%CTK_PATH%;customtkinter/" ^
  main.py
```

L'exécutable final se trouve dans `dist\ThirdEyeInsight.exe`.

> **Portable par conception** : où que tu déplaces l'exécutable compilé, les dossiers `decks/`, `exports/`, `imports/` et `.cache/` (base de cartes, images, logs) se créent automatiquement à côté de lui.

## Structure du projet

```
ThirdEyeInsight/
├── main.py              # Interface (customtkinter)
├── calculs.py            # Simulation Monte-Carlo et calculs hypergéométriques
├── translations.py       # Traductions (FR/EN/ES/DE/IT/PT)
├── requirements.txt
├── backend/               # Persistance, sécurité, appels API, import/export YDK
├── decks/                 # Créé au premier lancement — tes decks
├── exports/ , imports/    # Créés au premier lancement — échanges .ydk
└── .cache/                 # Créé au premier lancement — base de cartes, images, logs
```

## Tech stack

- [customtkinter](https://github.com/TomSchimansky/CustomTkinter) — interface graphique
- [pandas](https://pandas.pydata.org/) / [numpy](https://numpy.org/) — manipulation des données de deck et simulation vectorisée
- [scipy](https://scipy.org/) — loi hypergéométrique
- [Pillow](https://python-pillow.org/) — traitement des images de cartes
- [requests](https://requests.readthedocs.io/) — appels à l'API YGOPRODeck

## Tests

```bash
pip install pytest
pytest tests/ -v
```

## Remerciements

- [YGOPRODeck](https://ygoprodeck.com/) pour son API publique de base de cartes et d'images, utilisée conformément à ses [règles d'usage](https://ygoprodeck.com/api-guide/).

## Licence

[Voir la licence](LICENCE) pour le détail des conditions d'utilisation.

---

Yu-Gi-Oh! est une marque déposée de Konami. Ce projet est un outil non officiel créé par un fan, sans affiliation avec Konami ou Studio Dice.

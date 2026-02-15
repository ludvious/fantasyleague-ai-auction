import pandas as pd
from pathlib import Path
from typing import List, Optional
from loguru import logger
from core.models import Player, PlayerStatus, Position


class ExcelHandler:
    """Handler CRUD from dataset players"""
    
    REQUIRED_COLUMNS = ['Id', 'Nome', 'R', 'Squadra', 'Qt.A', 'FVM']
    NEW_COLUMNS = ['State', 'Picks_Count', 'Buyer_Id']
    
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        
    def validate_schema(self, df: pd.DataFrame) -> bool:
        """Valida lo schema del DataFrame"""
        
        # check required cols
        missing_cols = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        if missing_cols:
            logger.error(f"Colonne mancanti nel file Excel: {missing_cols}")
            return False
        
        # check if valid role/position
        ruoli_validi = {r.value for r in Position}
        ruoli_invalidi = set(df['ruolo'].unique()) - ruoli_validi
        if ruoli_invalidi:
            logger.error(f"Ruoli non validi trovati: {ruoli_invalidi}. Validi: {ruoli_validi}")
            return False
        
        # check unique id players
        if df['id'].duplicated().any():
            duplicati = df[df['id'].duplicated()]['id'].tolist()
            logger.error(f"ID duplicati trovati: {duplicati}")
            return False
        
        logger.info("Excel validato con successo")
        return True
    
    def load_players(self, validate: bool = True) -> List[Player]:
        """
        Load players from xlsx file.
        
        Args:
            validate: if True, validation schema before loading file.
            
        Returns:
            Player Object List
            
        Raises:
            FileNotFoundError: if file doesn't exist
            ValueError: if validation return false
        """
        
        if not self.filepath.exists():
            raise FileNotFoundError(f"File non trovato: {self.filepath}")
        
        logger.info(f"Caricamento giocatori da {self.filepath}")
        
        # load xlsx
        df = pd.read_excel(self.filepath, sheet_name="Tutti")
        logger.info(f"Caricati {len(df)} record dal file")
        
        # validation
        if validate and not self.validate_schema(df):
            raise ValueError("Validazione schema file Excel fallita ...")
        
        # Add new column new for the auction
        for col in self.NEW_COLUMNS:
            if col not in df.columns:
                if col == 'State':
                    df[col] = PlayerStatus.AVAILABLE.value
                if col == 'Pick_Count': #count how many times is pick from the list (uselful for start 2 round auction)
                    df[col] = 0
                if col == 'Buyer_Id':
                    df[col] = None
        
        players = []
        for _, row in df.iterrows():
            try:
                player = Player(
                    id=str(row['id']),
                    nome=str(row['Nome']),
                    ruolo=Position(row['R']),
                    squadra_reale=str(row['Squadra']),
                    quotazione=int(row['Qt.A']),
                    stato=PlayerStatus(row['State']),
                    picks_count=int(row['Picks_Count']),
                    buyer_id=str(row['Buyer_Id']),
                    prezzo_vendita=int(row['prezzo_vendita']) if pd.notna(row['prezzo_vendita']) else None
                )
                players.append(player)
            except Exception as e:
                logger.warning(f"Errore parsing giocatore {row.get('nome', 'unknown')}: {e}")
                continue
        
        logger.info(f"Caricati {len(players)} giocatori validi")
        
        return players
    
    def save_players(self, players: List[Player], backup: bool = True) -> None:
        """
        Save players to excel file.
        
        Args:
            players: List[Player]
            backup: if True, new backup file
        """
        # Backup se richiesto
        if backup and self.filepath.exists():
            backup_path = self.filepath.with_suffix('.xlsx.backup')
            import shutil
            shutil.copy2(self.filepath, backup_path)
            logger.info(f"Backup creato: {backup_path}")
        
        # to DataFrame
        data = []
        for player in players:
            data.append({
                'id': player.id,
                'name': player.name,
                'position': player.position.value,
                'team': player.team,
                'list_price': player.list_price,
                'sgate': player.state.value,
                'buyer_id': player.buyer_id,
                'selling_price': player.selling_price
            })
        
        df = pd.DataFrame(data)
        
        # Salva su Excel
        new_filepath = self.filepath + "AUCTION"
        df.to_excel(new_filepath, sheet_name="Tutti",index=False, engine='openpyxl')
        logger.info(f"Salvato {len(players)} giocatori su nuovo file {new_filepath}")
"""
Notary: CRUD manager for auction status.
Maintains team table, transaction log, and manages checkpoints.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from loguru import logger

from core.models import (
    Player, Team, Transaction, PlayerStatus,
    AuctionResult, ResultStatus, SimulationReport
)
from utils.excel_handler import ExcelHandler


class Notaio:
    """
    Auction manager.
    Responsible for:
    - Maintaining team tables
    - Recording transactions
    - Updating player status
    - Saving checkpoints
    - Generating reports
    """
    
    def __init__(
        self,
        excel_handler: ExcelHandler,
        checkpoint_dir: str = "data/checkpoints",
        checkpoint_interval: int = 10
    ):
        """
        Args:
            excel_handler:
            checkpoint_dir:
            checkpoint_interval:
        """
        self.excel_handler = excel_handler
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_interval = checkpoint_interval
        
        self.league_squads: Dict[str, Team] = {}
        self.transaction_log: List[Transaction] = []
        self.all_players: List[Player] = []
        self.timestamp_start: Optional[datetime] = None
        
        logger.info("Auction Manager ready ...")
    
    def new_squads(self, buyer_configs: List[dict], new_budget: int = 500) -> None:
        """
        Init buyer teams.
        
        Args:
            buyer_configs: List of buyer configurations [{id, name, personality}, ...]
            initial_budget: Initial budget for each buyer
        """
        for config in buyer_configs:
            team = Team(
                buyer_id=config['id'],
                buyer_name=config['name'],
                start_budget=new_budget,
                budget_rimanente=new_budget
            )
            self.league_squads[config['id']] = team
            logger.info(
                f"Squadra inizializzata: {team.buyer_name} "
                f"(Nome: {team.buyer_name}, Budget: {team.start_budget})"
            )
    
    def load_players_from_xlsx(self) -> List[Player]:
        """
        Load players from the Excel database.
        
        Returns:
            List of loaded players
        """
        self.all_players = self.excel_handler.load_players(validate=True)
        logger.info(f"Caricati {len(self.all_players)} giocatori dal database")
        return self.all_players
    
    def get_available_players(self) -> List[Player]:
        """Returns list of players with status AVAILABLE"""
        return [p for p in self.all_players if p.state == PlayerStatus.AVAILABLE]

    def record_purchase(
        self,
        buyer_id: str,
        player: Player,
        price: int,
        rivals_bids: Dict[str, int],
        is_tie_breaker: bool = False
    ) -> Transaction:
        """
        Record a purchase and update all statuses.
        
        Args:
            buyer_id: ID of the winning buyer
            player: Player purchased
            price: Purchase price
            rivals_bids: Dict with all bids
            is_tie_breaker: True if it is a raise
            
        Returns:
            Transaction object created
            
        Raises:
            ValueError: If invalid operation
        """
        
        team = self.league_squads.get(buyer_id)
        if not team:
            raise ValueError(f"Buyer {buyer_id} non trovato")
        
        # Aggiorna team (valida automaticamente)
        team.add_player(player=player, price=price)
        
        # new transaction
        transaction = Transaction(
            player=player,
            buyer_id=buyer_id,
            price=price,
            rivals_bids=rivals_bids,
            is_tie_breaker=is_tie_breaker
        )
        self.transaction_log.append(transaction)
        
        logger.success(
            f"Acquisto registrato: {player.name} → {team.buyer_name} "
            f"per {price} crediti (rosa: {team.team_size}/25)"
        )
        
        # Checkpoint automatico
        if len(self.transaction_log) % self.checkpoint_interval == 0:
            self.save_checkpoint()
        
        return transaction
    
    def update_player_status(self, player: Player, new_status: PlayerStatus, buyer_id: Optional[str] = None, price: Optional[int] = None) -> None:
        """
        Update a player's status.
        
        Args:
            player: Player to update
            new_status: New status
            buyer_id: Buyer ID (if sold)
            price: Price (if sold)
        """

        if new_status == PlayerStatus.SOLD:
            if buyer_id is None:
                raise ValueError("buyer_id richiesto per stato VENDUTO")
            player.buyer_id = buyer_id
            player.selling_price = price
            player.state = new_status
        
        logger.debug(f"Stato giocatore aggiornato: {player.name} → {new_status.value}")
    
    def update_database_xlsx(self, backup: bool = True) -> None:
        """
        Saves the updated player database to Excel.
        
        Args:
            backup: If True, creates a backup of the original file.
        """
        self.excel_handler.save_players(self.all_players, backup=backup)
        logger.info("Aggiornato file Lista Giocatori Excel Asta")
    
    def save_checkpoint(self, custom_name: Optional[str] = None) -> Path:
        """
        Saves the current state checkpoint.
        
        Args:
            custom_name: Custom name for the checkpoint (default: timestamp)
            
        Returns:
            Path of the created checkpoint file
        """
        
        if custom_name:
            filename = f"checkpoint_{custom_name}.json"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"checkpoint_{timestamp}.json"
        
        checkpoint_path = self.checkpoint_dir / filename
        
        checkpoint_data = {
            "timestamp": datetime.now().isoformat(),
            "num_transactions": len(self.transaction_log),
            "teams": {
                buyer_id: self.league_squads[Team].to_dict()
                for buyer_id, team in self.league_squads.items()
            },
            "players": [p.to_dict() for p in self.all_players],
            "transactions": [t.to_dict() for t in self.transaction_log]
        }
        
        with open(checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump(checkpoint_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Checkpoint salvato: {checkpoint_path}")
        return checkpoint_path
    
    def load_checkpoint(self, checkpoint_path: str) -> None:
        """
        Load state from checkpoint (for recovery).
        
        Args:
            checkpoint_path: Path of the checkpoint file
            
        Raises:
            FileNotFoundError: If checkpoint does not exist
        """
        
        path = Path(checkpoint_path)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint non trovato: {checkpoint_path}")
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        logger.info(f"Caricamento checkpoint: {checkpoint_path}")
        
        # Ricostruisci stato
        # TODO: Implementare ricostruzione completa da checkpoint
        # (necessita deserializzazione da dict a oggetti Pydantic)
        
        logger.warning("Load checkpoint non completamente implementato")
    
    def generate_report(self) -> SimulationReport:
        """
        Generates the final simulation report.
        
        Returns:
            SimulationReport object
        """
        
        timestamp_end = datetime.now()
        duration = (timestamp_end - self.timestamp_start).total_seconds() if self.timestamp_start else 0
        
        # Conta stati giocatori
        venduti = [p for p in self.all_players if p.state == PlayerStatus.SOLD]
        invenduti = [p for p in self.all_players if p.state == PlayerStatus.UNSOLD]
        
        report = SimulationReport(
            timestamp_start=self.timestamp_start or timestamp_end,
            timestamp_end=timestamp_end,
            duration_seconds=duration,
            teams=self.league_squads.copy(),
            transactions=self.transaction_log.copy(),
            unsold_players=invenduti,
            total_players=len(self.all_players),
            players_sold=len(venduti),
            players_unsold=len(invenduti)
        )
        
        logger.info("Report generato")
        return report
    
    def save_report(self, report: SimulationReport, output_dir: str = "data/risults") -> tuple[Path, Path]:
        """
        Save report to file (JSON + TXT).
        
        Args:
            report: Report to save
            output_dir: Output directory
            
        Returns:
            Tuple (path_json, path_txt)
        """
        
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = report.timestamp_end.strftime("%Y%m%d_%H%M%S")
        
        # save JSON
        json_path = output_path / f"report_{timestamp}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
        
        # Salva TXT (human-readable)
        txt_path = output_path / f"report_{timestamp}.txt"
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write("REPORT SIMULAZIONE FANTACALCIO AI\n")
            f.write("=" * 70 + "\n\n")
            
            f.write(f"Data: {report.timestamp_end.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Durata: {report.duration_seconds:.2f} secondi\n\n")
            
            f.write("STATISTICHE GENERALI\n")
            f.write("-" * 70 + "\n")
            f.write(f"Giocatori totali:     {report.total_players}\n")
            f.write(f"Giocatori venduti:    {report.players_sold}\n")
            f.write(f"Giocatori invenduti:  {report.players_unsold}\n")
            f.write(f"Spesa totale:         {report.total_spent} crediti\n")
            f.write(f"Prezzo massimo:       {report.max_price} crediti\n")
            
            f.write("SQUADRE\n")
            f.write("=" * 70 + "\n")
            for team in report.teams.values():
                f.write(f"\n{team.buyer_name} ({team.buyer_id})\n")
                f.write("-" * 70 + "\n")
                f.write(f"Rosa Totale:       {team.team_size}/25 giocatori\n")
                f.write(f"Budget rimanente:  {team.budget_rimanente} crediti\n")
                f.write(f"Spesa totale:      {team.total_expense} crediti\n")
                f.write(f"Prezzo medio:      {team.price_media:.2f} crediti\n")
                
                split_by_role = team.player_for_position()
                f.write(f"Rosa:      Portieri:{split_by_role['P']} Difensori:{split_by_role['D']} "
                       f"Cntrocampisti:{split_by_role['C']} Attaccanti:{split_by_role['A']}\n")
                
                f.write("\nGiocatori:\n")
                for player in team.players:
                    f.write(f"  - {player.name:<25} {player.position.value}  "
                           f"{player.team:<15} Q:{player.list_price:2d}  "
                           f"→ {player.selling_price:3d} cr\n")
            
            if report.unsold_players:
                f.write("\n" + "=" * 70 + "\n")
                f.write("GIOCATORI INVENDUTI\n")
                f.write("=" * 70 + "\n")
                for player in report.unsold_players:
                    f.write(f"  - {player.name:<25} {player.position.value}  "
                           f"{player.team:<15} Q:{player.list_price:2d}\n")
        
        logger.success(f"Report salvato: {json_path} e {txt_path}")
        return json_path, txt_path
    
    def get_statistics(self) -> dict:
        """Restituisce statistiche in tempo reale"""
        available = self.get_available_players()
        solded = [p for p in self.all_players if p.state == PlayerStatus.SOLD]
        
        return {
            "giocatori_disponibili": len(available),
            "giocatori_venduti": len(solded),
            "transazioni_totali": len(self.transaction_log),
            "rose_complete": sum(1 for t in self.league_squads.values() if t.is_complete)
        }
"""Read-only Aerodrome Voter calls used to verify Sugar-derived data.

We only read in V1: gauges, isAlive, maxVotingNum, epochVoteEnd, lastVoted.
No `vote(...)` submission. No private keys touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable

from web3 import Web3

VOTER_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "gauges",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "isAlive",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "maxVotingNum",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "_timestamp", "type": "uint256"}],
        "name": "epochVoteEnd",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "name": "lastVoted",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "weights",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass(frozen=True)
class GaugeStatus:
    pool: str
    gauge: str           # 0x0 if no gauge
    alive: bool
    weight_raw: int      # on-chain veAERO weight, 18 decimals

    @property
    def has_gauge(self) -> bool:
        return int(self.gauge, 16) != 0


@dataclass(frozen=True)
class VoterState:
    max_voting_num: int
    epoch_vote_end_ts: int  # unix seconds


class VoterClient:
    def __init__(self, rpc_uri: str, voter_addr: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_uri))
        self.contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(voter_addr),
            abi=VOTER_ABI,
        )

    def state(self) -> VoterState:
        now = self.w3.eth.get_block("latest")["timestamp"]
        return VoterState(
            max_voting_num=int(self.contract.functions.maxVotingNum().call()),
            epoch_vote_end_ts=int(self.contract.functions.epochVoteEnd(now).call()),
        )

    def gauge_status(self, pools: Iterable[str]) -> Dict[str, GaugeStatus]:
        out: Dict[str, GaugeStatus] = {}
        for p in pools:
            pool_cs = Web3.to_checksum_address(p)
            gauge = self.contract.functions.gauges(pool_cs).call()
            if int(gauge, 16) == 0:
                out[p.lower()] = GaugeStatus(
                    pool=p.lower(), gauge=gauge, alive=False, weight_raw=0
                )
                continue
            alive = bool(self.contract.functions.isAlive(gauge).call())
            weight = int(self.contract.functions.weights(pool_cs).call())
            out[p.lower()] = GaugeStatus(
                pool=p.lower(), gauge=gauge, alive=alive, weight_raw=weight
            )
        return out

    def last_voted(self, venft_id: int) -> int:
        return int(self.contract.functions.lastVoted(venft_id).call())

import csv
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run():
    """Run the full wallet evaluation cycle."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("Export it: export ANTHROPIC_API_KEY=sk-ant-...")
        return

    from lib.db import init_db, get_connection
    from lib.normalizers import normalize_wallet, normalize_game
    from lib.analyzer import build_wallet_profile, format_wallet_for_prompt
    from lib import memory

    if not memory.is_available():
        print("ERROR: Mem0 is not available. The evaluate command requires Mem0 for continuity.")
        print("Install mem0ai: pip install mem0ai")
        return

    init_db()
    conn = get_connection()

    # Step 1: Build evaluation scope
    # Active wallets from CSV
    csv_path = os.path.join(BASE_DIR, 'active_wallets.csv')
    active_wallets = {}
    try:
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = normalize_wallet(row['address'])
                if addr == '__global__':
                    continue
                game = normalize_game(row.get('market_whitelist', ''), source='whitelist')
                active_wallets[addr] = game
    except FileNotFoundError:
        print("ERROR: active_wallets.csv not found")
        conn.close()
        return

    # Wallets with trade data
    trade_wallets = set()
    for row in conn.execute("SELECT DISTINCT master_wallet FROM trades").fetchall():
        trade_wallets.add(row['master_wallet'])

    eval_scope = set(active_wallets.keys()) | trade_wallets
    print(f"Evaluation scope: {len(eval_scope)} wallets "
          f"({len(active_wallets)} active, {len(trade_wallets)} with trades)")

    # Step 2: Catch up retirement memories
    caught_up = memory.catch_up_retirements(conn)
    if caught_up:
        print(f"  Caught up {caught_up} retirement memories in Mem0")

    # Step 3: "What's new since last eval"
    last_eval = conn.execute("SELECT MAX(eval_date) FROM evaluation_log").fetchone()[0]
    is_first_eval = last_eval is None

    if is_first_eval:
        total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        total_sims = conn.execute("SELECT COUNT(*) FROM sim_registry").fetchone()[0]
        whats_new = (f"This is your first evaluation. Total data: {total_trades} trades, "
                     f"{total_sims} sim(s), {len(active_wallets)} active wallets.")
    else:
        new_ingests = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(new_trades), 0) FROM ingest_registry WHERE ingested_at > ?",
            (last_eval,)
        ).fetchone()
        new_sims = conn.execute(
            "SELECT COUNT(*) FROM sim_registry WHERE ingested_at > ?", (last_eval,)
        ).fetchone()[0]
        new_changes = conn.execute(
            "SELECT COUNT(*) FROM wallet_changes WHERE change_date > ?", (last_eval,)
        ).fetchone()[0]
        whats_new = (f"Since your last evaluation on {last_eval}: "
                     f"{new_ingests[0]} new ingest(s) ({new_ingests[1]} trades), "
                     f"{new_sims} new sim(s), {new_changes} wallet change(s).")

    # Step 4: Build wallet profiles
    print("Building wallet profiles...")
    profiles = []
    for wallet in eval_scope:
        profile = build_wallet_profile(conn, wallet)
        profile['in_csv'] = wallet in active_wallets
        profile['filter_game'] = active_wallets.get(wallet, None)
        profiles.append(profile)

    # Step 5: Query Mem0
    print("Querying Mem0 for context...")
    pattern_memories = memory.search("general patterns esports trading wallets")
    wallet_memories = {}
    for wallet in eval_scope:
        mems = memory.search(f"wallet {wallet[:12]}")
        if mems:
            wallet_memories[wallet] = mems

    # Step 6: Build prompt
    criteria_path = os.path.join(BASE_DIR, 'wallet_criteria.md')
    with open(criteria_path) as f:
        criteria = f.read()

    # Format wallet data
    wallet_sections = []
    for profile in profiles:
        section = format_wallet_for_prompt(profile)
        # Add Mem0 context if available
        wallet = profile['wallet']
        if wallet in wallet_memories:
            section += "\nMemory context:"
            mems = wallet_memories[wallet]
            if isinstance(mems, dict):
                mems = mems.get('results', mems.get('memories', [mems]))
            if not isinstance(mems, list):
                mems = [mems]
            for m in mems[:3]:
                if isinstance(m, dict):
                    mem_text = m.get('memory', m.get('text', str(m)))
                else:
                    mem_text = str(m)
                section += f"\n  - {mem_text[:200]}"
        wallet_sections.append(section)

    # Active wallet list
    active_list = "\n".join(
        f"- {addr[:10]}...{addr[-4:]} ({game})"
        for addr, game in active_wallets.items()
    )

    system_prompt = f"""You are a Polymarket esports copy-trading wallet curator agent.
Your job is to recommend which wallets to add to, remove from, or keep
in the copy-trading CSV based on data analysis and your accumulated knowledge.

{criteria}

You will receive:
1. A "what's new" summary showing data changes since your last evaluation
2. Per-wallet data packets with real P&L, weekly trajectory, sim profiles, and behavioral flags
3. Each wallet's latest sharp sim reference number
4. A flag indicating if the sim profile is complete or partial
5. Relevant memories from your past evaluations (including retired wallet profiles)
6. The current active wallet list with their game filters

Output JSON:
{{
  "adds": [{{"wallet": "0x...", "game": "CS2", "sim_number": 1, "reasoning": "..."}}],
  "removes": [{{"wallet": "0x...", "game": "LOL", "sim_number": 1, "reasoning": "..."}}],
  "keeps": [{{"wallet": "0x...", "game": "CS2", "sim_number": 1, "reasoning": "..."}}],
  "watch": [{{"wallet": "0x...", "game": "VALO", "sim_number": 1, "concerns": "..."}}],
  "pattern_observations": ["new general patterns noticed"],
  "wallet_observations": [{{"wallet": "0x...", "observation": "..."}}]
}}

KEEP = wallet is in CSV and performing well, actively endorsed to stay.
pattern_observations and wallet_observations get stored in long-term memory.
Write them as notes to your future self — be specific, include data points.
If a wallet has an incomplete sim profile, note that in your reasoning.
If a wallet has incomplete position data (sells without matching buys), note that P&L may be understated."""

    user_prompt = f"""## What's New
{whats_new}

## Current Active Wallets ({len(active_wallets)})
{active_list}

## Wallet Data
{''.join(wallet_sections)}

## Pattern Memories
"""
    if pattern_memories:
        if isinstance(pattern_memories, dict):
            pattern_memories = pattern_memories.get('results', pattern_memories.get('memories', []))
        if not isinstance(pattern_memories, list):
            pattern_memories = [pattern_memories]
        for m in pattern_memories[:5]:
            if isinstance(m, dict):
                mem_text = m.get('memory', m.get('text', str(m)))
            else:
                mem_text = str(m)
            user_prompt += f"- {mem_text[:300]}\n"
    else:
        user_prompt += "No prior pattern memories.\n"

    # Step 7: Call Claude API
    print("Calling Claude API...")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_response = response.content[0].text
    except Exception as e:
        print(f"ERROR: Claude API call failed: {e}")
        # Save debug data
        debug_path = os.path.join(BASE_DIR, 'reports', f"eval_debug_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.txt")
        with open(debug_path, 'w') as f:
            f.write(f"Error: {e}\n\nSystem prompt:\n{system_prompt}\n\nUser prompt:\n{user_prompt}")
        print(f"Debug data saved to {debug_path}")
        conn.close()
        return

    # Step 8: Parse response
    try:
        # Try to extract JSON from response
        json_start = raw_response.find('{')
        json_end = raw_response.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            result = json.loads(raw_response[json_start:json_end])
        else:
            raise ValueError("No JSON found in response")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"WARNING: Could not parse JSON response: {e}")
        result = {"raw": raw_response}

    # Step 9: Store in Mem0
    if 'pattern_observations' in result:
        for obs in result['pattern_observations']:
            memory.add(obs, metadata={"type": "pattern"})

    if 'wallet_observations' in result:
        for obs in result['wallet_observations']:
            if isinstance(obs, dict):
                memory.add(
                    obs.get('observation', str(obs)),
                    metadata={"type": "wallet_observation", "wallet": obs.get('wallet', '')}
                )

    # Step 10: Generate report
    now = datetime.now()
    report_path = os.path.join(
        BASE_DIR, 'reports', f"eval_{now.strftime('%Y-%m-%d_%H%M%S')}.md"
    )

    report = _generate_report(result, profiles, whats_new, now)

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w') as f:
        f.write(report)

    # Step 11: Log evaluation
    conn.execute("""
        INSERT INTO evaluation_log (wallets_evaluated, adds_recommended, removes_recommended,
            keeps_recommended, watches_recommended, report_path, raw_response)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        len(eval_scope),
        len(result.get('adds', [])),
        len(result.get('removes', [])),
        len(result.get('keeps', [])),
        len(result.get('watch', [])),
        report_path,
        raw_response,
    ))
    conn.commit()
    conn.close()

    print(report)
    print(f"\nSaved to {report_path}")


def _generate_report(result, profiles, whats_new, now):
    """Generate the evaluation markdown report."""
    lines = [f"# Wallet Curator Report — {now.strftime('%Y-%m-%d %H:%M')}"]
    lines.append(f"\n**What's new:** {whats_new}")

    # KEEP
    keeps = result.get('keeps', [])
    lines.append(f"\n## KEEP ({len(keeps)})")
    for k in keeps:
        wallet = k.get('wallet', '?')
        game = k.get('game', '?')
        sim = k.get('sim_number', '?')
        lines.append(f"✅ **{wallet[:10]}...** ({game}) — Sharp Sim #{sim}")
        lines.append(f"> {k.get('reasoning', 'No reasoning provided')}")

    # ADD
    adds = result.get('adds', [])
    lines.append(f"\n## ADD ({len(adds)})")
    for a in adds:
        wallet = a.get('wallet', '?')
        game = a.get('game', '?')
        sim = a.get('sim_number', '?')
        lines.append(f"🟢 **{wallet[:10]}...** ({game}) — Sharp Sim #{sim}")
        lines.append(f"> {a.get('reasoning', 'No reasoning provided')}")

    # REMOVE
    removes = result.get('removes', [])
    lines.append(f"\n## REMOVE ({len(removes)})")
    for r in removes:
        wallet = r.get('wallet', '?')
        game = r.get('game', '?')
        sim = r.get('sim_number', '?')
        lines.append(f"🔴 **{wallet[:10]}...** ({game}) — Sharp Sim #{sim}")
        lines.append(f"> {r.get('reasoning', 'No reasoning provided')}")

    # WATCH
    watch = result.get('watch', [])
    lines.append(f"\n## WATCH ({len(watch)})")
    for w in watch:
        wallet = w.get('wallet', '?')
        game = w.get('game', '?')
        sim = w.get('sim_number', '?')
        lines.append(f"🟡 **{wallet[:10]}...** ({game}) — Sharp Sim #{sim}")
        lines.append(f"> {w.get('concerns', 'No concerns provided')}")

    # Patterns
    patterns = result.get('pattern_observations', [])
    if patterns:
        lines.append(f"\n## Pattern Observations")
        for p in patterns:
            lines.append(f"- {p}")

    return '\n'.join(lines)

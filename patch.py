import sys

with open("api/main.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_target_block = False

# The target block starts after line 208 (which is `token_filter = None`)
# Wait, let's identify the exact lines based on string matching.
found_start = False
found_end = False

for i, line in enumerate(lines):
    if line.strip() == "current_chunk_start = start_dt" and not found_start:
        found_start = True
        
        # Insert generator start
        new_lines.extend([
            "        from fastapi.responses import StreamingResponse\n",
            "        import json\n",
            "        import asyncio\n",
            "        import gc\n",
            "        import psycopg2\n",
            "\n",
            "        async def generate():\n",
            "            c_chunk_start = start_dt\n",
            "            has_data = False\n",
            "            total_seconds = (end_dt - start_dt).total_seconds()\n",
            "            \n",
            "            while c_chunk_start < end_dt:\n",
            "                c_chunk_end = min(c_chunk_start + timedelta(days=1), end_dt)\n",
            "                progress_sec = (c_chunk_start - start_dt).total_seconds()\n",
            "                pct = (progress_sec / total_seconds) * 100 if total_seconds > 0 else 0\n",
            "                yield json.dumps({\"type\": \"progress\", \"pct\": round(pct, 1), \"message\": f\"Fetching {c_chunk_start.strftime('%Y-%m-%d')}...\"}) + \"\\n\"\n",
            "                await asyncio.sleep(0.01)\n",
            "                \n",
            "                print(f\"[Anaylsis] Processing batch: {c_chunk_start} -> {c_chunk_end}\")\n",
            "                batch_swaps = fetcher.fetch_swaps(c_chunk_start, c_chunk_end, token_filter=token_filter, network=network)\n",
            "                \n",
            "                if batch_swaps:\n",
            "                    has_data = True\n",
            "                    analyzer.process_batch(batch_swaps, start_tokens_list, end_tokens_list)\n",
            "                    \n",
            "                batch_swaps = []\n",
            "                gc.collect()\n",
            "                c_chunk_start = c_chunk_end\n",
            "                \n",
            "            yield json.dumps({\"type\": \"progress\", \"pct\": 100, \"message\": \"Finalizing analysis...\"}) + \"\\n\"\n",
            "            await asyncio.sleep(0.01)\n",
            "            \n",
            "            if not has_data:\n",
            "                conn = psycopg2.connect(DATA_WAREHOUSE_DB)\n",
            "                cur = conn.cursor()\n",
            "                cur.execute(\"\"\"\n",
            "                    SELECT MIN(timestamp), MAX(timestamp) FROM (\n",
            "                        SELECT timestamp FROM uniswap_v3_swaps\n",
            "                        UNION ALL\n",
            "                        SELECT timestamp FROM uniswap_v4_swaps\n",
            "                    ) as all_swaps\n",
            "                \"\"\")\n",
            "                row = cur.fetchone()\n",
            "                db_min = row[0].isoformat() if row[0] else None\n",
            "                db_max = row[1].isoformat() if row[1] else None\n",
            "                cur.close()\n",
            "                conn.close()\n",
            "                \n",
            "                yield json.dumps({\"type\": \"result\", \"data\": {\"routes\": [], \"total_tx\": 0, \"total_volume\": 0, \"db_range\": {\"min\": db_min, \"max\": db_max}}}) + \"\\n\"\n",
            "                return\n",
            "\n"
        ])
        in_target_block = True
        continue
        
    if in_target_block:
        if line.strip() == "analysis = analyzer.get_results()":
            in_target_block = False # Wait, now we indent the rest of the block!
            new_lines.append("            " + line.lstrip())
        else:
            # Skip the old while loop and if not has_data block
            pass
            
    elif not in_target_block and found_start and not found_end:
        # We are after the original skipped block, so we are at `analysis = analyzer.get_results()`
        if line.strip() == "return analysis":
            new_lines.append("            yield json.dumps({\"type\": \"result\", \"data\": analysis}) + \"\\n\"\n")
            new_lines.append("\n        return StreamingResponse(generate(), media_type=\"application/x-ndjson\")\n")
            found_end = True
        else:
            # Indent by adding 4 spaces! The base indentation is 8 spaces, we want 12 spaces.
            # Original indentation is 8 spaces.
            if line.strip() == "":
                new_lines.append(line)
            else:
                new_lines.append("    " + line)
                
    else:
        new_lines.append(line)

with open("api/main.py", "w") as f:
    f.writelines(new_lines)


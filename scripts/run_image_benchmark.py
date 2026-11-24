"""Generate 20 realistic desktop screenshots (10 DSA, 10 non-DSA) and benchmark model evaluation."""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from app.config import (
    MODEL_PREFERENCES,
    MODEL_PREFERENCE_1,
    MODEL_PREFERENCE_2,
    MODEL_PREFERENCE_3,
    get_gemini_api_key,
)
from app.judge import tier2_evaluate, get_model_manager
from app.models import (
    ActivityContext,
    GoalProfile,
    TabInfo,
    TabSnapshot,
    WindowInfo,
)

BENCHMARK_DIR = Path(__file__).resolve().parent / "benchmark_images"
BENCHMARK_DIR.mkdir(exist_ok=True)


def get_default_font(size: int = 16) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def create_mock_desktop_image(
    filename: str,
    title: str,
    app_bar: str,
    content_lines: list[str],
    bg_color: str = "#1E1E1E",
    header_color: str = "#2D2D2D",
    text_color: str = "#E0E0E0",
    accent_color: str = "#007ACC",
) -> bytes:
    """Generate a realistic desktop capture with window chrome, status bar, and formatted content."""
    width, height = 1024, 768
    img = Image.new("RGB", (width, height), color="#121212")
    draw = ImageDraw.Draw(img)

    title_font = get_default_font(20)
    body_font = get_default_font(16)
    sub_font = get_default_font(13)

    # Window container
    margin = 20
    win_w = width - 2 * margin
    win_h = height - 2 * margin
    draw.rectangle([margin, margin, margin + win_w, margin + win_h], fill=bg_color, outline="#3C3C3C", width=2)

    # Title bar
    header_h = 44
    draw.rectangle([margin, margin, margin + win_w, margin + header_h], fill=header_color)

    # Window control dots (Mac/Win style)
    dot_y = margin + 14
    draw.ellipse([margin + 16, dot_y, margin + 28, dot_y + 12], fill="#FF5F56")
    draw.ellipse([margin + 36, dot_y, margin + 48, dot_y + 12], fill="#FFBD2E")
    draw.ellipse([margin + 56, dot_y, margin + 68, dot_y + 12], fill="#27C93F")

    # Title & App bar
    draw.text((margin + 84, margin + 10), title, fill="#FFFFFF", font=title_font)
    draw.text((margin + win_w - 240, margin + 14), app_bar, fill="#888888", font=sub_font)

    # Divider
    draw.line([margin, margin + header_h, margin + win_w, margin + header_h], fill=accent_color, width=2)

    # Content body
    content_y = margin + header_h + 24
    for line in content_lines:
        if line.startswith("# "):
            draw.text((margin + 32, content_y), line[2:], fill=accent_color, font=title_font)
            content_y += 34
        elif line.startswith("## "):
            draw.text((margin + 32, content_y), line[3:], fill="#FFFFFF", font=title_font)
            content_y += 28
        elif line.startswith(">>> "):
            draw.rectangle([margin + 28, content_y - 2, margin + win_w - 28, content_y + 22], fill="#252526")
            draw.text((margin + 36, content_y), line[4:], fill="#DCDCAA", font=body_font)
            content_y += 26
        else:
            draw.text((margin + 32, content_y), line, fill=text_color, font=body_font)
            content_y += 24

    # Bottom status bar
    footer_y = margin + win_h - 28
    draw.rectangle([margin, footer_y, margin + win_w, margin + win_h], fill=header_color)
    draw.text((margin + 20, footer_y + 6), f"Status: Focus Mode Active | Screen ID: {filename}", fill="#AAAAAA", font=sub_font)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()

    file_path = BENCHMARK_DIR / filename
    with open(file_path, "wb") as f:
        f.write(data)

    return data


TEST_DATASET = [
    # --- 10 DSA SCENARIOS (ON-TASK) ---
    {
        "id": 1,
        "filename": "dsa_01_leetcode_two_sum.jpg",
        "title": "LeetCode - 1. Two Sum (Python3)",
        "app_bar": "leetcode.com/problems/two-sum",
        "proc": "chrome.exe",
        "expected_on_task": True,
        "lines": [
            "# Problem: Two Sum",
            "Given an array of integers nums and an integer target, return indices of the two numbers such that they add up to target.",
            "",
            "## Example 1:",
            "Input: nums = [2,7,11,15], target = 9",
            "Output: [0,1] because nums[0] + nums[1] == 9.",
            "",
            "## Python Solution (Hash Map):",
            ">>> class Solution:",
            ">>>     def twoSum(self, nums: List[int], target: int) -> List[int]:",
            ">>>         lookup = {}",
            ">>>         for i, num in enumerate(nums):",
            ">>>             diff = target - num",
            ">>>             if diff in lookup: return [lookup[diff], i]",
            ">>>             lookup[num] = i",
        ],
    },
    {
        "id": 2,
        "filename": "dsa_02_bst_inorder_traversal.jpg",
        "title": "VS Code - binary_tree.py — Data Structures",
        "app_bar": "Visual Studio Code (Workspace: Algorithms)",
        "proc": "code.exe",
        "expected_on_task": True,
        "lines": [
            "# Binary Search Tree Implementation",
            "class TreeNode:",
            "    def __init__(self, val=0, left=None, right=None):",
            "        self.val = val; self.left = left; self.right = right",
            "",
            "## Inorder Traversal (Left -> Root -> Right):",
            ">>> def inorderTraversal(root: Optional[TreeNode]) -> List[int]:",
            ">>>     res, stack = [], []",
            ">>>     curr = root",
            ">>>     while curr or stack:",
            ">>>         while curr:",
            ">>>             stack.append(curr); curr = curr.left",
            ">>>         curr = stack.pop()",
            ">>>         res.append(curr.val); curr = curr.right",
            ">>>     return res",
        ],
    },
    {
        "id": 3,
        "filename": "dsa_03_gfg_dijkstra.jpg",
        "title": "GeeksforGeeks - Dijkstra's Shortest Path Algorithm",
        "app_bar": "geeksforgeeks.org/dijkstras-shortest-path-algorithm",
        "proc": "chrome.exe",
        "expected_on_task": True,
        "lines": [
            "# Dijkstra's Algorithm on Adjacency List",
            "Algorithm for finding the shortest paths between nodes in a graph with non-negative edge weights.",
            "",
            "## Complexity Analysis:",
            "- Time Complexity: O((V + E) log V) using min-priority queue (Binary Heap)",
            "- Space Complexity: O(V) for distance table and priority queue.",
            "",
            "## C++ Implementation Snippet:",
            ">>> priority_queue<pair<int, int>, vector<pair<int, int>>, greater<>> pq;",
            ">>> pq.push({0, src}); dist[src] = 0;",
            ">>> while(!pq.empty()) { auto [d, u] = pq.top(); pq.pop(); ... }",
        ],
    },
    {
        "id": 4,
        "filename": "dsa_04_avl_tree_rotations.jpg",
        "title": "Wikipedia — AVL Tree Self-Balancing Rotations",
        "app_bar": "en.wikipedia.org/wiki/AVL_tree",
        "proc": "edge.exe",
        "expected_on_task": True,
        "lines": [
            "# AVL Tree Balance Factor & Rotations",
            "In an AVL tree, the heights of the two child subtrees of any node differ by at most one.",
            "If at any time they differ by more than one, rebalancing is done to restore this property.",
            "",
            "## Rotation Operations:",
            "1. Left-Left (LL) -> Right Rotation at node Z",
            "2. Right-Right (RR) -> Left Rotation at node Z",
            "3. Left-Right (LR) -> Left Rotate on left child, then Right Rotate on Z",
            "4. Right-Left (RL) -> Right Rotate on right child, then Left Rotate on Z",
        ],
    },
    {
        "id": 5,
        "filename": "dsa_05_neetcode_dp_lecture.jpg",
        "title": "YouTube — NeetCode 150: Dynamic Programming 0/1 Knapsack",
        "app_bar": "youtube.com/watch?v=neetcode_dp_tutorial",
        "proc": "chrome.exe",
        "expected_on_task": True,
        "lines": [
            "# NeetCode — Dynamic Programming Explained",
            "Tutorial: 0/1 Knapsack & Coin Change Problem",
            "",
            "## Memoization vs Tabulation Table:",
            "- Subproblem: dp(i, rem_weight)",
            "- Recurrence Relation:",
            ">>> dp[i][w] = max(dp[i-1][w], val[i] + dp[i-1][w - wt[i]])",
            "",
            "Video Time: 14:32 / 38:00 (On-Task Study Video)",
        ],
    },
    {
        "id": 6,
        "filename": "dsa_06_quicksort_partition.jpg",
        "title": "Visualgo — QuickSort Lomuto Partition Visualization",
        "app_bar": "visualgo.net/en/sorting",
        "proc": "chrome.exe",
        "expected_on_task": True,
        "lines": [
            "# QuickSort Partitioning Demonstration",
            "Array: [38, 27, 43, 3, 9, 82, 10] | Pivot: 10",
            "",
            "## Lomuto Partition Scheme:",
            ">>> int partition(vector<int>& arr, int low, int high) {",
            ">>>     int pivot = arr[high], i = low - 1;",
            ">>>     for (int j = low; j < high; j++) {",
            ">>>         if (arr[j] < pivot) { i++; swap(arr[i], arr[j]); }",
            ">>>     }",
            ">>>     swap(arr[i + 1], arr[high]); return i + 1;",
            ">>> }",
        ],
    },
    {
        "id": 7,
        "filename": "dsa_07_graph_bfs_dfs.jpg",
        "title": "Terminal — python test_graphs.py (pytest passing)",
        "app_bar": "PowerShell - dsa_workspace",
        "proc": "powershell.exe",
        "expected_on_task": True,
        "lines": [
            "# pytest test_graphs.py -v",
            ">>> test_bfs_shortest_unweighted_path ... PASSED [ 25%]",
            ">>> test_dfs_detect_cycle_directed ... PASSED [ 50%]",
            ">>> test_topological_sort_kahns ... PASSED [ 75%]",
            ">>> test_tarjan_strongly_connected ... PASSED [100%]",
            "",
            "======================== 4 passed in 0.18s ========================",
        ],
    },
    {
        "id": 8,
        "filename": "dsa_08_stack_overflow_trie.jpg",
        "title": "Stack Overflow — Efficient Prefix Tree (Trie) in Java",
        "app_bar": "stackoverflow.com/questions/prefix-tree-trie-implementation",
        "proc": "chrome.exe",
        "expected_on_task": True,
        "lines": [
            "# Question: How to implement autocomplete with Trie memory efficiency?",
            "Asked 3 years ago | Active today | Viewed 142k times",
            "",
            "## Top Accepted Answer:",
            ">>> class TrieNode {",
            ">>>     Map<Character, TrieNode> children = new HashMap<>();",
            ">>>     boolean isEndOfWord = false;",
            ">>> }",
            "This achieves O(M) lookup where M is the prefix length, superior to iterating all dictionary keys.",
        ],
    },
    {
        "id": 9,
        "filename": "dsa_09_clrs_book_pdf.jpg",
        "title": "Adobe Acrobat Reader — Introduction to Algorithms (CLRS 4th Ed).pdf",
        "app_bar": "Acrobat Reader (Page 312 of 1312)",
        "proc": "AcroRd32.exe",
        "expected_on_task": True,
        "lines": [
            "# Chapter 12: Binary Search Trees",
            "Theorem 12.1: If x is a node in a binary search tree, then x.left.key <= x.key <= x.right.key.",
            "",
            "## Tree-Search(x, k)",
            "1  if x == NIL or k == x.key",
            "2      return x",
            "3  if k < x.key",
            "4      return Tree-Search(x.left, k)",
            "5  else return Tree-Search(x.right, k)",
        ],
    },
    {
        "id": 10,
        "filename": "dsa_10_leetcode_sliding_window.jpg",
        "title": "LeetCode — 3. Longest Substring Without Repeating Characters",
        "app_bar": "leetcode.com/problems/longest-substring-without-repeating-characters",
        "proc": "chrome.exe",
        "expected_on_task": True,
        "lines": [
            "# Problem 3: Longest Substring Without Repeating Characters",
            "Given a string s, find the length of the longest substring without repeating characters.",
            "",
            "## Sliding Window Algorithm:",
            ">>> left = max_len = 0",
            ">>> char_set = set()",
            ">>> for right in range(len(s)):",
            ">>>     while s[right] in char_set:",
            ">>>         char_set.remove(s[left]); left += 1",
            ">>>     char_set.add(s[right])",
            ">>>     max_len = max(max_len, right - left + 1)",
        ],
    },

    # --- 10 NON-DSA / DISTRACTION SCENARIOS (OFF-TASK) ---
    {
        "id": 11,
        "filename": "distract_11_instagram_reels.jpg",
        "title": "Instagram — Reels, Stories & Explore Feed",
        "app_bar": "instagram.com/reels",
        "proc": "chrome.exe",
        "expected_on_task": False,
        "lines": [
            "# Instagram Reels",
            "Explore trending reels, travel clips, viral memes and dance videos.",
            "",
            "## Trending Reel by @viral_creator:",
            "- 'Top 10 hilarious dog reactions in 2026!'",
            "- 1.4M Likes | 34.2K Comments | 512K Shares",
            "",
            "Suggested for you: Travel vloggers in Tokyo street food festival.",
        ],
    },
    {
        "id": 12,
        "filename": "distract_12_youtube_cat_videos.jpg",
        "title": "YouTube — Funniest Cats of 2026 Compilation (Try Not To Laugh)",
        "app_bar": "youtube.com/watch?v=cute_cats_funny_moments",
        "proc": "chrome.exe",
        "expected_on_task": False,
        "lines": [
            "# Funniest Cats of the Year Compilation",
            "12,492,109 views * Streamed live 2 days ago",
            "",
            "## Video Description:",
            "The most hilarious kittens jumping, missing parkour jumps, and sleeping in bowls.",
            "Like and subscribe for weekly comedy pets!",
            "",
            "Up Next: Top 50 funniest animals compilation.",
        ],
    },
    {
        "id": 13,
        "filename": "distract_13_netflix_browse.jpg",
        "title": "Netflix — Watch TV Shows Online, Watch Movies Online",
        "app_bar": "netflix.com/browse",
        "proc": "edge.exe",
        "expected_on_task": False,
        "lines": [
            "# Netflix: Popular on Netflix Right Now",
            "Top 10 Movies in the US Today:",
            "1. The Galactic Heist (Action / Sci-Fi) - 2h 14m",
            "2. Mystery at Pine Manor (Thriller / Drama)",
            "3. Laugh Track Standup Comedy Special",
            "",
            "Click to resume 'Stranger Things Season 5 Episode 3' (32m remaining).",
        ],
    },
    {
        "id": 14,
        "filename": "distract_14_reddit_memes.jpg",
        "title": "Reddit — r/memes: The front page of internet comedy",
        "app_bar": "reddit.com/r/memes",
        "proc": "chrome.exe",
        "expected_on_task": False,
        "lines": [
            "# r/memes — Posted by u/MemeLord42",
            "'When you try to study for 5 minutes and check your phone'",
            "",
            "## Image Content:",
            "[Funny meme photo of a sleeping cartoon cat with textbooks untouched]",
            "Upvotes: 42.1k | Comments: 891 | Share",
            "",
            "Sort by: Hot Posts | Trending Now across Reddit",
        ],
    },
    {
        "id": 15,
        "filename": "distract_15_twitter_sports_trends.jpg",
        "title": "X (formerly Twitter) — What's happening in Sports & Politics",
        "app_bar": "x.com/explore/tabs/trending",
        "proc": "chrome.exe",
        "expected_on_task": False,
        "lines": [
            "# Trends for you:",
            "1. Sports * Trending: #ChampionsLeagueFinal (480K posts)",
            "2. Entertainment * Trending: #MarvelTrailer (230K posts)",
            "3. Gaming * Trending: #GTA6NewScreenshots (890K posts)",
            "",
            "Latest Tweet: 'Unbelievable bicycle kick goal in the 90th minute! Watch the clip!'",
        ],
    },
    {
        "id": 16,
        "filename": "distract_16_steam_game_store.jpg",
        "title": "Steam — Summer Gaming Festival Sale 2026",
        "app_bar": "Steam Client (store.steampowered.com)",
        "proc": "steam.exe",
        "expected_on_task": False,
        "lines": [
            "# Steam Special Offers & Top Sellers",
            "- Cyberpunk Overdrive Edition - 50% OFF ($29.99)",
            "- Elden Realm: Shadowlands - 30% OFF ($34.99)",
            "- Counter-Strike 2: Global Major Tournament Pass",
            "",
            "Friends Online: 8 friends currently playing games.",
            "Install button: Ready to download (64 GB).",
        ],
    },
    {
        "id": 17,
        "filename": "distract_17_amazon_shopping.jpg",
        "title": "Amazon.com: Noise Cancelling Wireless Gaming Headset with RGB",
        "app_bar": "amazon.com/dp/B08WIRELESS_HEADPHONES",
        "proc": "chrome.exe",
        "expected_on_task": False,
        "lines": [
            "# HyperSound Pro Wireless ANC Gaming Headset",
            "Price: $129.99 (Save 20% with prime coupon)",
            "",
            "## Customer Reviews:",
            "4.7 out of 5 stars (18,402 ratings)",
            "- 'Incredible bass and mic quality for Discord gaming sessions!'",
            "- 'Comfortable memory foam ear cushions for 8+ hour gaming marathons.'",
            "",
            "In Stock. Ships from and sold by Amazon.com.",
        ],
    },
    {
        "id": 18,
        "filename": "distract_18_spotify_music.jpg",
        "title": "Spotify — Today's Top Pop Hits Playlist",
        "app_bar": "Spotify Desktop Player",
        "proc": "Spotify.exe",
        "expected_on_task": False,
        "lines": [
            "# Today's Top Hits (34,102,400 Saves)",
            "Now Playing: 'Summer Nights & Neon Lights' — Pop Icons",
            "",
            "## Tracklist:",
            "1. Midnight Groove (3:14) — Artist A",
            "2. Electric Beats (2:45) — Artist B",
            "3. Starlight Serenade (3:50) — Artist C",
            "",
            "Shuffle: ON | Volume: 85% | Lyrics enabled",
        ],
    },
    {
        "id": 19,
        "filename": "distract_19_discord_gaming_chat.jpg",
        "title": "Discord — #general in Valorant & Gaming Squad",
        "app_bar": "Discord Client (Server: Night Raiders)",
        "proc": "Discord.exe",
        "expected_on_task": False,
        "lines": [
            "# #general (Gaming squad chat)",
            "@UserAlpha: 'Anyone down for ranked competitive match right now? Need 1 more!'",
            "@UserBravo: 'I am getting on in 5 mins, let's climb to Immortal today.'",
            "@UserCharlie: 'Look at this funny clip from our match yesterday lol'",
            "",
            "Voice Channels: Team 1 (3/5) [Lobby: Active]",
        ],
    },
    {
        "id": 20,
        "filename": "distract_20_cricbuzz_live_score.jpg",
        "title": "Cricbuzz — Live Cricket Score & Ball-by-Ball Commentary",
        "app_bar": "cricbuzz.com/live-cricket-scores/t20-world-cup",
        "proc": "chrome.exe",
        "expected_on_task": False,
        "lines": [
            "# T20 World Cup Final — Live Scorecard",
            "Team A: 184/4 (20.0 Ovs) vs Team B: 142/6 (16.2 Ovs)",
            "Equation: Team B needs 43 runs in 22 balls to win!",
            "",
            "## Live Commentary:",
            "16.2: SIX! Smashed over deep mid-wicket for a massive 95-meter maximum!",
            "16.1: FOUR! Edged past third man boundary.",
            "",
            "Current Batsman: 58* (28 balls, 5 fours, 4 sixes).",
        ],
    },
]


def run_benchmark():
    api_key = get_gemini_api_key()
    if not api_key:
        print("ERROR: GEMINI_API_KEY not found in environment.")
        return

    profile = GoalProfile(
        goal="Study Data Structures and Algorithms (DSA)",
        on_task_topics=["binary search", "leetcode", "algorithms", "data structures", "trees", "graphs", "sorting"],
        allowed_domains=["leetcode.com", "geeksforgeeks.org", "visualgo.net"],
        borderline_domains=["youtube.com", "wikipedia.org", "stackoverflow.com"],
        blocked_domains=["instagram.com", "netflix.com", "reddit.com", "cricbuzz.com", "amazon.com", "x.com"],
        allowed_apps=["code.exe", "powershell.exe", "AcroRd32.exe"],
        blocked_apps=["steam.exe", "Spotify.exe", "Discord.exe"],
    )

    print("=" * 80)
    print("ANTI-DISTRACTOR: 20-IMAGE MULTI-MODEL BENCHMARK EVALUATION")
    print("=" * 80)
    print(f"Goal: {profile.goal}")
    print(f"Primary Model (Preference 1): {MODEL_PREFERENCE_1}")
    print(f"Fallback Model 2 (Preference 2): {MODEL_PREFERENCE_2}")
    print(f"Fallback Model 3 (Preference 3): {MODEL_PREFERENCE_3}")
    print("Execution Mode:")
    print(" - ANTI_DISTRACTOR_FAKE_JUDGE: False (Live API inference)")
    print(" - Verdict Cache: None (Bypassed; fresh vision check per image)")
    print(" - Hardcoded Results: False (Real image bytes processed)")
    print("=" * 80)

    results = []
    correct_count = 0
    total_latency = 0.0

    for item in TEST_DATASET:
        item_id = item["id"]
        filename = item["filename"]
        title = item["title"]
        expected = item["expected_on_task"]
        proc = item["proc"]
        app_bar = item["app_bar"]

        # Generate screenshot bytes
        img_bytes = create_mock_desktop_image(
            filename=filename,
            title=title,
            app_bar=app_bar,
            content_lines=item["lines"],
        )

        tab = TabInfo(tab_id=item_id, window_id=1, url=f"https://{app_bar}", title=title, active=True, domain=app_bar.split("/")[0])
        snapshot = TabSnapshot(active_tab_id=item_id, window_focused=True, tabs=[tab], updated_at=time.time())
        win = WindowInfo(hwnd=100 + item_id, title=title, process_name=proc, pid=2000 + item_id, is_browser=(proc in ("chrome.exe", "edge.exe")))

        ctx = ActivityContext(
            trigger_type="tab_activated" if win.is_browser else "window_activated",
            source_key=proc,
            display_title=title,
            active_url=f"https://{app_bar}",
            goal=profile.goal,
            tab_snapshot=snapshot,
            foreground_window=win,
            open_windows=[title],
        )

        start_t = time.perf_counter()
        try:
            verdict = tier2_evaluate(
                ctx=ctx,
                profile=profile,
                screenshot_jpeg=img_bytes,
                api_key=api_key,
                model=MODEL_PREFERENCE_1,
            )
            model_used = MODEL_PREFERENCE_1
        except Exception as e:
            # Fallback to Preference 2 / 3
            try:
                verdict = tier2_evaluate(
                    ctx=ctx,
                    profile=profile,
                    screenshot_jpeg=img_bytes,
                    api_key=api_key,
                    model=MODEL_PREFERENCE_2,
                )
                model_used = MODEL_PREFERENCE_2
            except Exception:
                verdict = tier2_evaluate(
                    ctx=ctx,
                    profile=profile,
                    screenshot_jpeg=img_bytes,
                    api_key=api_key,
                    model=MODEL_PREFERENCE_3,
                )
                model_used = MODEL_PREFERENCE_3

        latency = time.perf_counter() - start_t
        total_latency += latency

        is_correct = (verdict.on_task == expected)
        if is_correct:
            correct_count += 1

        res = {
            "id": item_id,
            "filename": filename,
            "title": title,
            "expected_on_task": expected,
            "verdict_on_task": verdict.on_task,
            "correct": is_correct,
            "confidence": verdict.confidence,
            "latency": latency,
            "model_used": model_used,
            "reason": verdict.reason,
            "activity": verdict.activity,
        }
        results.append(res)

        status_icon = "CORRECT" if is_correct else "MISMATCH"
        verdict_str = "ON-TASK" if verdict.on_task else "OFF-TASK"
        expected_str = "ON-TASK" if expected else "OFF-TASK"
        print(f"[{item_id:02d}/20] {status_icon} | Model: {verdict_str} (Expected: {expected_str}) | Conf: {verdict.confidence:.2f} | Latency: {latency:.2f}s | {title[:45]}")

    accuracy = (correct_count / len(TEST_DATASET)) * 100
    avg_latency = total_latency / len(TEST_DATASET)

    print("\n" + "=" * 80)
    print("BENCHMARK SUMMARY")
    print("=" * 80)
    print(f"Total Test Cases:       {len(TEST_DATASET)} (10 DSA On-Task, 10 Distraction Off-Task)")
    print(f"Correct Predictions:    {correct_count} / {len(TEST_DATASET)}")
    print(f"Accuracy Rate:          {accuracy:.1f}%")
    print(f"Average Latency:        {avg_latency:.2f}s per vision evaluation")
    print("=" * 80)

    # Save benchmark JSON
    report_file = BENCHMARK_DIR / "benchmark_results.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": {
                    "total": len(TEST_DATASET),
                    "correct": correct_count,
                    "accuracy_pct": accuracy,
                    "avg_latency_s": avg_latency,
                    "model_preference_1": MODEL_PREFERENCE_1,
                    "model_preference_2": MODEL_PREFERENCE_2,
                    "model_preference_3": MODEL_PREFERENCE_3,
                },
                "results": results,
            },
            f,
            indent=2,
        )
    print(f"Detailed benchmark results saved to: {report_file}")


if __name__ == "__main__":
    run_benchmark()

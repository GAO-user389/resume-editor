# -*- coding: utf-8 -*-
"""批量测试 15 份简历的解析完整性：PDF 原文 vs 解析后各板块内容，找出内容丢失"""
import re, os, glob, statistics
import pdfplumber

ATTACH_DIR = r'c:\Users\DELL\.trae-cn\attachments'
PDFS = glob.glob(os.path.join(ATTACH_DIR, '*.pdf'))

SECTION_MARKERS = [
    {'key': 'education',  'pats': ['教育背景','教育经历','学习经历','学历','EDUCATION','Education']},
    {'key': 'internship', 'pats': ['实习和工作经历','实习经历','实习经验','工作经历','工作经验','实践经历','校园经历','校园活动','校园实践','社会实践','工作实践','科研经历','科研项目','INTERNSHIP','Internship','EXPERIENCE','Experience','WORK EXPERIENCE','Work Experience']},
    {'key': 'projects',   'pats': ['项目经历','项目经验','PROJECTS','Projects','PROJECT','Project']},
    {'key': 'skills',     'pats': ['技能与证书','专业技能','技能','技术能力','技术栈','证书','荣誉奖项','获得荣誉','奖项荣誉','获奖经历','获奖情况','个人荣誉','荣誉证书','自我评价','个人优势','个人简介','个人总结','兴趣爱好','论文发表','学术成果','SKILLS','Skills','CERTIFICATE','Certificate','Certificates']},
]
ALL_MARKER_WORDS = [p for m in SECTION_MARKERS for p in m['pats']]
HEADER_MODIFIERS = ['核心','专业','个人','主要','基本','相关','通用','其他','各类','多项','特长','概述','简介','素质']
SEMANTIC_HEADING_KW = ['经历','经验','技能','能力','荣誉','奖项','证书','总结','项目','教育','学历','论文','成果','实践','活动']

def escape_reg(s): return re.escape(s)

# ===== parseFile 模拟 =====
def parse_file(pdf_path):
    text = ''
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False)
            if not words:
                continue
            # 过滤垃圾文字
            def is_garbage(s):
                if re.search(r'[\u4e00-\u9fa5]', s): return False
                if not re.match(r'^[a-zA-Z0-9_\-~]+$', s): return False
                if len(s) <= 2 or len(s) >= 15: return True
                # BOSS 水印碎片：数字+小写字母的 2 字符组合（如 3t、t3、7f），可带尾部 -
                if re.match(r'^(\d[a-z]|[a-z]\d)-?$', s): return True
                return False
            pw = page.width
            ph = page.height
            words = [w for w in words if w['x0'] >= -5 and w['x0'] <= pw + 5 and not is_garbage(w['text'])]
            if not words:
                continue
            # 仅用页面中部(15%~85%高度)的 words 判定 gutter，避开横跨两栏的头部
            mid_words = [w for w in words if ph * 0.15 < w['top'] < ph * 0.85]
            gutter_src = mid_words if len(mid_words) > 10 else words
            xs = sorted(w['x0'] for w in gutter_src)
            gutter = None
            if len(xs) > 20:
                max_gap = 0; gap_at = None
                for k in range(1, len(xs)):
                    gap = xs[k] - xs[k-1]
                    mid = (xs[k] + xs[k-1]) / 2
                    if pw*0.25 < mid < pw*0.75 and gap > max_gap:
                        max_gap = gap; gap_at = mid
                if max_gap > pw * 0.04: gutter = gap_at
            # 含日期且横跨两栏的行，整体并入左栏
            all_heights = [w['bottom']-w['top'] for w in words if w['bottom']-w['top']>0]
            all_med_h = statistics.median(all_heights) if all_heights else 3
            y_tol = max(2, all_med_h * 0.5)
            spanning_bands = set()
            if gutter is not None:
                y_groups = {}
                for w in words:
                    yk = round(w['top'] / y_tol)
                    y_groups.setdefault(yk, []).append(w)
                for yk, band in y_groups.items():
                    has_left = any(w['x0'] < gutter for w in band)
                    has_right = any(w['x0'] >= gutter for w in band)
                    if has_left and has_right: spanning_bands.add(yk)
            left = []
            right = []
            if gutter is None:
                left = words
            else:
                for w in words:
                    yk = round(w['top'] / y_tol)
                    if yk in spanning_bands:
                        left.append(w)
                    elif w['x0'] < gutter:
                        left.append(w)
                    else:
                        right.append(w)
            for col in (left, right):
                if not col: continue
                heights = [w['bottom']-w['top'] for w in col if w['bottom']-w['top']>0]
                med_h = statistics.median(heights) if heights else 3
                tol = max(2, med_h*0.5)
                space_gap = max(1, med_h*0.2)
                col.sort(key=lambda w: (round(w['top']/tol)*tol, w['x0']))
                lines = []; cur = []; last_top = None
                for w in col:
                    if last_top is not None and abs(w['top']-last_top) >= tol:
                        lines.append(cur); cur = []
                    cur.append(w); last_top = w['top']
                if cur: lines.append(cur)
                for ln in lines:
                    parts = []; last_end = None
                    for w in ln:
                        if last_end is not None and w['x0'] - last_end > space_gap:
                            parts.append(' ')
                        parts.append(w['text'])
                        last_end = w['x1']
                    text += ''.join(parts).rstrip() + '\n'
    sorted_markers = sorted(ALL_MARKER_WORDS, key=len, reverse=True)
    alts = []
    for m in sorted_markers:
        if re.match(r'^[\x00-\x7F]+$', m):
            alts.append(r'\b' + escape_reg(m) + r'\b')
        else:
            alts.append(r'(?<![\u4e00-\u9fa5])' + escape_reg(m) + r'(?![\u4e00-\u9fa5])')
    alt = '|'.join(alts)
    text = re.sub(r'[ \t]*(' + alt + r')[ \t]*', r'\n\1\n', text, flags=re.I)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ===== isSectionMarkerLine =====
def is_section_marker_line(line):
    if not line: return False
    orig = line.strip()
    t = orig
    for kw in ALL_MARKER_WORDS:
        t = re.sub(re.escape(kw), '', t, flags=re.I)
    t = re.sub(r'[\s·•|｜\-–—:：、,，.。（）()【】\[\]《》<>]', '', t)
    if len(t) <= 1: return True
    if len(orig) < 15 and t in HEADER_MODIFIERS: return True
    # 动态语义兜底
    if len(orig) <= 12 and not re.search(r'[，,。.；;：:]$', orig):
        if any(kw in orig for kw in SEMANTIC_HEADING_KW): return True
    return False

# ===== splitResumeSections =====
def has_kw(line, kw):
    if re.search(r'[\u4e00-\u9fa5]', kw): return kw in line
    return kw.lower() in line.lower()

def is_url_fragment(line, prev, nxt):
    if not line: return False
    if re.search(r'[\u4e00-\u9fa5]', line): return False
    if re.match(r'^https?://', line, re.I): return True
    if ' ' not in line and re.search(r'\.[a-z]{2,}', line, re.I): return True
    if ' ' not in line and (line.startswith('-') or line.endswith('-')): return True
    prev_is_url = bool(prev) and (re.match(r'^https?://', prev, re.I) or (' ' not in prev and re.search(r'\.[a-z]{2,}', prev, re.I)) or (' ' not in prev and prev.endswith('-')))
    next_is_url = bool(nxt) and (' ' not in nxt and (nxt.startswith('-') or re.search(r'\.[a-z]{2,}', nxt, re.I)))
    if ' ' not in line and (prev_is_url or next_is_url): return True
    return False

def match_section(line):
    for m in SECTION_MARKERS:
        for p in m['pats']:
            if has_kw(line, p): return m['key']
    return None

def split_resume_sections(text):
    raw_lines = [l.strip() for l in text.split('\n') if l.strip()]
    lines = []
    i = 0
    while i < len(raw_lines):
        if i + 1 < len(raw_lines):
            merged = raw_lines[i] + raw_lines[i+1]
            if is_section_marker_line(merged) and not is_section_marker_line(raw_lines[i]) and not is_section_marker_line(raw_lines[i+1]):
                lines.append(merged); i += 2; continue
        lines.append(raw_lines[i]); i += 1
    sections = {k: [] for k in ['header','education','internship','projects','skills']}
    header_end = 0
    for idx, l in enumerate(lines):
        prev = lines[idx-1] if idx > 0 else ''
        nxt = lines[idx+1] if idx+1 < len(lines) else ''
        if is_url_fragment(l, prev, nxt):
            sections['header'].append(l); header_end = idx+1; continue
        if is_section_marker_line(l) and match_section(l):
            header_end = idx; break
        sections['header'].append(l); header_end = idx+1
    current = None
    for idx in range(header_end, len(lines)):
        l = lines[idx]
        prev = lines[idx-1] if idx > 0 else ''
        nxt = lines[idx+1] if idx+1 < len(lines) else ''
        if not is_url_fragment(l, prev, nxt):
            matched = match_section(l) if is_section_marker_line(l) else None
            if matched: current = matched
        if current: sections[current].append(l)
    # 兜底：板块无标记时，从 header 中提取对应内容（避免无标记简历的内容丢失）
    edu_kw = re.compile(r'大学|学院|学校|主修课程|本科|硕士|博士|大专|专业')
    int_kw = re.compile(r'公司|集团|银行|工作室|事务所|有限公司')
    if not sections['education']:
        for l in sections['header']:
            if edu_kw.search(l) and not l.startswith(('手机','电话','邮箱','姓名','地址','出生')):
                sections['education'].append(l)
    if not sections['internship']:
        for l in sections['header']:
            if int_kw.search(l) and not edu_kw.search(l):
                sections['internship'].append(l)
    return {k: '\n'.join(v).strip() for k, v in sections.items()}

# ===== cleanSection =====
def clean_section(s):
    def is_url_frag(l):
        l = l.strip()
        if not l: return False
        if re.match(r'^https?://', l, re.I): return True
        if ' ' not in l and re.search(r'\.[a-z]{2,}', l, re.I): return True
        if ' ' not in l and (l.startswith('-') or l.endswith('-')): return True
        return False
    raw = s.split('\n'); merged = []
    for l in raw:
        prev_is_url = merged and is_url_frag(merged[-1])
        cur_no_space = l.strip() and ' ' not in l.strip()
        if prev_is_url and cur_no_space:
            merged[-1] = merged[-1] + l.strip()
        else:
            merged.append(l)
    return '\n'.join(l for l in merged if not is_section_marker_line(l)).strip()

# ===== parseEntries =====
DATE_RE = re.compile(r'(\d{4}(?:年[/\-.]?\d{1,2}月?|[/\-.]\d{1,2})?\s*[\-–至\s]+\s*(?:\d{4}(?:年[/\-.]?\d{1,2}月?|[/\-.]\d{1,2})?|至今|现在))')
def has_date(s): return bool(DATE_RE.search(s))
def is_bullet(s): return bool(re.match(r'^[•·\u2022\-]\s', s)) or bool(re.match(r'^[•·\u2022]', s)) or bool(re.match(r'^\d+[\.、)]\s', s))
def is_header_like(s): return not is_bullet(s) and len(s) < 35 and not re.search(r'[，,。.；;]', s) and not re.search(r'[：:]\s*$', s)
def strip_bullet(s): return re.sub(r'^[•·\u2022\-]\s*', '', re.sub(r'^\d+[\.、)]\s*', '', s, count=1))
ENTITY_SUFFIXES = ['公司','有限公司','分公司','集团','银行','大学','学院','学校','工作室','事务所']
def is_entity_header(s):
    if re.match(r'^项目名称', s): return True
    return any(s.endswith(suf) for suf in ENTITY_SUFFIXES)

def parse_entries(text):
    if not text: return []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    date_idxs = [i for i, l in enumerate(lines) if has_date(l)]
    entries = []
    if not date_idxs:
        ch, cd = [], []
        for l in lines:
            if is_bullet(l):
                cd.append(strip_bullet(l))
            elif is_entity_header(l):
                if ch or cd: entries.append({'header':' '.join(ch),'desc':'\n'.join(cd)})
                ch, cd = [l], []
            elif is_header_like(l) and not cd:
                ch.append(l)
            else:
                cd.append(l)
        if ch or cd: entries.append({'header':' '.join(ch),'desc':'\n'.join(cd)})
        return entries
    for k, di in enumerate(date_idxs):
        next_di = date_idxs[k+1] if k+1 < len(date_idxs) else len(lines)
        hl = []
        for j in range(di-1, max(di-2, -1), -1):
            if is_bullet(lines[j]) or has_date(lines[j]) or not is_header_like(lines[j]): break
            hl.insert(0, lines[j])
        hl.append(lines[di])
        he = di + 1
        for j in range(di+1, next_di):
            if is_bullet(lines[j]) or has_date(lines[j]) or not is_header_like(lines[j]): break
            hl.append(lines[j]); he = j + 1
        dl = lines[he:next_di]
        while dl and is_header_like(dl[-1]) and not has_date(dl[-1]): dl.pop()
        desc = '\n'.join(strip_bullet(l) if is_bullet(l) else l for l in dl)
        entries.append({'header':' '.join(hl), 'desc':desc})
    return entries

def normalize(s):
    """归一化文本用于比较：去所有空白和标点"""
    return re.sub(r'[\s·•|｜\-–—:：、,，.。（）()【】\[\]《》<>·•]', '', s)

def test_pdf(pdf_path):
    name = os.path.basename(pdf_path)
    try:
        raw = parse_file(pdf_path)
    except Exception as e:
        return name, f'PARSE_ERROR: {e}', []
    if not raw.strip():
        return name, 'EMPTY_TEXT（可能是扫描件）', []
    secs = split_resume_sections(raw)
    cleaned = {k: clean_section(v) for k, v in secs.items()}
    # 把所有板块内容拼起来
    all_parsed = '\n'.join([cleaned['header'], cleaned['education'], cleaned['internship'], cleaned['projects'], cleaned['skills']])
    # 完整性检查：原文每个非空行（归一化后）是否都出现在解析结果中
    raw_lines = [l for l in raw.split('\n') if l.strip()]
    parsed_norm = normalize(all_parsed)
    missing = []
    for l in raw_lines:
        ln = normalize(l)
        if len(ln) < 2: continue
        # 跳过板块标记行（cleanSection 会故意删除）
        if is_section_marker_line(l): continue
        if ln not in parsed_norm:
            missing.append(l)
    # 统计条目数
    edu_n = len(parse_entries(cleaned['education']))
    int_n = len(parse_entries(cleaned['internship']))
    proj_n = len(parse_entries(cleaned['projects']))
    skill_lines = [l for l in cleaned['skills'].split('\n') if l.strip() and len(l.strip())>2]
    return name, 'OK', {
        'raw_lines': len(raw_lines),
        'missing': missing,
        'edu': edu_n, 'int': int_n, 'proj': proj_n, 'skills': len(skill_lines),
        'header_preview': cleaned['header'][:80],
    }

if __name__ == '__main__':
    print(f"共 {len(PDFS)} 份 PDF\n")
    results = []
    for pdf in sorted(PDFS):
        name, status, detail = test_pdf(pdf)
        results.append((name, status, detail))
        if status != 'OK':
            print(f"❌ {name}: {status}")
        else:
            miss_n = len(detail['missing'])
            flag = '✓' if miss_n == 0 else '⚠'
            print(f"{flag} {name}")
            print(f"    原文行数={detail['raw_lines']} 缺失={miss_n} | 教育={detail['edu']} 实习={detail['int']} 项目={detail['proj']} 技能行={detail['skills']}")
            if miss_n > 0:
                for m in detail['missing'][:5]:
                    print(f"      缺: {m[:60]}")
                if miss_n > 5:
                    print(f"      ...共 {miss_n} 行缺失")
            print(f"    header: {detail['header_preview']}")

"""Small, original five-finger exercises and transparent progress rules."""

from dataclasses import dataclass

from midi_keyboard import note_name


SOLFEGE = {0: "Do", 2: "Re", 4: "Mi", 5: "Fa", 7: "Sol", 9: "La", 11: "Si"}
FINGER_NAMES = {1: "拇指", 2: "食指", 3: "中指", 4: "无名指", 5: "小指"}


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    offsets: tuple
    hand: str
    hint: str
    rhythm: bool = False

    def notes(self, base):
        return [frozenset({base + offset}) for offset in self.offsets]

    def fingers(self, base):
        fingers = (5, 4, 3, 2, 1) if self.hand == "左手" else (1, 2, 3, 4, 5)
        return dict(zip((base + n for n in (0, 2, 4, 5, 7)), fingers))


LESSONS = (
    Lesson("find_c", "1 · 认识 C", (0, 0, 0, 0), "右手", "两枚黑键左边的白键是 C；每次松开后再按。"),
    Lesson("right_five", "2 · 右手五指", (0, 2, 4, 5, 7, 5, 4, 2, 0), "右手", "拇指放 C，五指依次放 C–G，轻松落键。"),
    Lesson("left_five", "3 · 左手五指", (0, 2, 4, 5, 7, 5, 4, 2, 0), "左手", "换左手，小指放 C，拇指放 G；仍在同一组白键练习。"),
    Lesson("steady_beat", "4 · 跟拍单音", (0, 0, 0, 0, 2, 2, 2, 2), "右手", "先听四拍，再一拍一音；跟不上可减速。节拍不计分。", True),
    Lesson("first_melody", "5 · 第一段旋律", (0, 2, 4, 2, 0, 4, 7, 4, 5, 4, 2, 0), "右手", "先听示范，再分段练习；弹错时先找准键位。"),
)


class BeginnerCourse:
    """Four-note chunks; two clean rounds permit an explicit move onward.

    These are adjustable product defaults, not evidence-based mastery thresholds.
    """

    def __init__(self, lesson, base, chunk_size=4, required_rounds=2):
        self.lesson = lesson
        self.base = base
        notes = lesson.notes(base)
        self.chunks = [notes[i:i + chunk_size] for i in range(0, len(notes), chunk_size)]
        self.chunk_index = 0
        self.clean_rounds = 0
        self.required_rounds = required_rounds
        self.attempts = 0
        self.errors = 0

    @property
    def groups(self):
        return self.chunks[self.chunk_index]

    @property
    def ready(self):
        return self.clean_rounds >= self.required_rounds

    @property
    def complete(self):
        return self.ready and self.chunk_index == len(self.chunks) - 1

    def finish_round(self, errors):
        self.attempts += 1
        self.errors += errors
        self.clean_rounds = self.clean_rounds + 1 if errors == 0 else 0

    def advance(self):
        if not self.ready or self.complete:
            return False
        self.chunk_index += 1
        self.clean_rounds = 0
        return True

    def target_text(self, note):
        finger = self.lesson.fingers(self.base).get(note)
        return f"{SOLFEGE.get(note % 12, '')} {note_name(note)} · {self.lesson.hand} {finger} 指（{FINGER_NAMES[finger]}）"


GUIDE_HTML = """
<h3>先把一小段弹清楚</h3>
<p>先认识 C，再分别练右手、左手五指。手指编号：拇指 1，食指 2，中指 3，无名指 4，小指 5。</p>
<p>听一次示范，慢慢弹一小段。遇到错音，先找准键，再重弹；熟悉后尝试看音名、不依赖高亮。</p>
<p>节拍只用来听和跟随，不评判节奏。先听四拍，再试一拍一音；难以跟上就减速或先关闭节拍。</p>
<p>分成几次短练，隔天再复习。每次先定一个小目标，结束时回想最难的地方。坐稳，放松肩膀与手腕；不舒服就停下休息。</p>
<p>本软件能检查按键，不能判断姿势、实际使用哪根手指、触键质量或完整钢琴水平。建议指法仅用于这里的固定五指练习。</p>
<p>每段最多 4 音、连续 2 轮无错后进入下一段，是软件练习规则，不代表已经掌握钢琴技能。</p>
"""

SOURCES_HTML = """
<h3>练习设计的参考</h3>
<p><a href="https://doi.org/10.1177/0022429408328851">Duke、Simmons 与 Cash（2009）：It's Not How Much; It's How</a><br>
17 名高年级本科及研究生钢琴专业学生的观察研究。练习质量与次日表现有关，不能推断为所有零基础学生的因果规律。</p>
<p><a href="https://doi.org/10.1177/0022429411424798">Simmons（2012，2011 在线发表）：Distributed Practice and Procedural Memory Consolidation in Musicians’ Skill Learning</a><br>
29 名非钢琴专业的音乐学习者练习 9 音序列；比较 5 分钟、6 小时与 24 小时练习间隔，提示分次练习和休息可能有益。</p>
<p><a href="https://doi.org/10.1177/0305735617731614">McPherson 等（2018，2017 在线发表）：Applying self-regulated learning microanalysis to study musicians’ practice</a><br>
两名大学音乐学生的案例分析，支持用目标、执行和反思来组织练习，不是初学者干预效果试验。</p>
<p><a href="https://www.abrsm.org/en-gb/for-learners/piano-learning/initialgrade">ABRSM · Initial Grade 学习资源</a><br>
提供听奏、找音、五指位置、数拍及节拍器等教学活动；属于教学参考，不是实验研究。</p>
<p>核对日期：2026-09-17。前三篇依据出版元数据与摘要整理，未将摘要当作全文系统综述。课程音型为本项目自行设计。</p>
"""

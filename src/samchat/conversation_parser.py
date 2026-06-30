import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from .base_agent import Message


class ConversationParser:
    def __init__(self):
        self.mention_pattern = re.compile(r'@(\w+)')
        self.task_pattern = re.compile(r'\[task\](.*?)\[/task\]', re.IGNORECASE | re.DOTALL)
        self.priority_pattern = re.compile(r'\b(high|medium|low)\s+priority\b', re.IGNORECASE)
        self.deadline_pattern = re.compile(r'\b(by|before|deadline|due)\s+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+ \d{1,2})', re.IGNORECASE)
        self.blocker_keywords = ['blocked', 'blocker', 'stuck', 'waiting on', 'depends on', 'need help']
        self.decision_keywords = ['decided', 'decision', 'agreed', 'will do', 'let\'s go with', 'approved']

    def parse_conversation(self, raw_text: str) -> List[Message]:
        messages = []
        lines = raw_text.split('\n')

        current_sender = None
        current_content = []

        for line in lines:
            sender_match = re.match(r'^(\w+):\s*(.*)', line)

            if sender_match:
                if current_sender and current_content:
                    messages.append(Message(
                        role="user",
                        content=' '.join(current_content),
                        sender=current_sender,
                        timestamp=datetime.now().timestamp()
                    ))

                current_sender = sender_match.group(1)
                current_content = [sender_match.group(2)] if sender_match.group(2) else []
            elif line.strip() and current_sender:
                current_content.append(line.strip())

        if current_sender and current_content:
            messages.append(Message(
                role="user",
                content=' '.join(current_content),
                sender=current_sender,
                timestamp=datetime.now().timestamp()
            ))

        return messages

    def extract_mentions(self, text: str) -> List[str]:
        return self.mention_pattern.findall(text)

    def extract_tasks(self, text: str) -> List[str]:
        explicit_tasks = self.task_pattern.findall(text)

        implicit_tasks = []
        task_indicators = ['need to', 'should', 'must', 'will', 'going to', 'plan to']
        sentences = text.split('.')

        for sentence in sentences:
            for indicator in task_indicators:
                if indicator in sentence.lower():
                    implicit_tasks.append(sentence.strip())
                    break

        return explicit_tasks + implicit_tasks

    def extract_priorities(self, text: str) -> List[Tuple[str, str]]:
        priorities = []
        matches = self.priority_pattern.finditer(text)

        for match in matches:
            priority_level = match.group(1).lower()
            context_start = max(0, match.start() - 50)
            context_end = min(len(text), match.end() + 50)
            context = text[context_start:context_end]
            priorities.append((priority_level, context))

        return priorities

    def extract_deadlines(self, text: str) -> List[Tuple[str, str]]:
        deadlines = []
        matches = self.deadline_pattern.finditer(text)

        for match in matches:
            deadline_text = match.group(2)
            context_start = max(0, match.start() - 50)
            context_end = min(len(text), match.end() + 50)
            context = text[context_start:context_end]
            deadlines.append((deadline_text, context))

        return deadlines

    def identify_blockers(self, text: str) -> List[str]:
        blockers = []
        sentences = text.split('.')

        for sentence in sentences:
            sentence_lower = sentence.lower()
            for keyword in self.blocker_keywords:
                if keyword in sentence_lower:
                    blockers.append(sentence.strip())
                    break

        return blockers

    def identify_decisions(self, text: str) -> List[str]:
        decisions = []
        sentences = text.split('.')

        for sentence in sentences:
            sentence_lower = sentence.lower()
            for keyword in self.decision_keywords:
                if keyword in sentence_lower:
                    decisions.append(sentence.strip())
                    break

        return decisions

    def extract_entities(self, conversation: List[Message]) -> Dict[str, Any]:
        all_text = ' '.join([msg.content for msg in conversation])

        return {
            "participants": list(set([msg.sender for msg in conversation if msg.sender])),
            "mentions": self.extract_mentions(all_text),
            "tasks": self.extract_tasks(all_text),
            "priorities": self.extract_priorities(all_text),
            "deadlines": self.extract_deadlines(all_text),
            "blockers": self.identify_blockers(all_text),
            "decisions": self.identify_decisions(all_text),
            "message_count": len(conversation),
            "conversation_length": len(all_text)
        }

    def segment_by_topic(self, conversation: List[Message]) -> List[Dict[str, Any]]:
        segments = []
        current_segment = []
        topic_keywords = {
            "planning": ["sprint", "backlog", "story", "epic", "planning"],
            "technical": ["code", "api", "database", "architecture", "implementation"],
            "testing": ["test", "qa", "bug", "issue", "defect"],
            "deployment": ["deploy", "release", "production", "staging"],
            "meeting": ["standup", "retrospective", "review", "demo"]
        }

        for msg in conversation:
            current_segment.append(msg)

            msg_lower = msg.content.lower()
            detected_topics = []

            for topic, keywords in topic_keywords.items():
                if any(keyword in msg_lower for keyword in keywords):
                    detected_topics.append(topic)

            if len(current_segment) >= 5 or (detected_topics and len(current_segment) >= 2):
                segments.append({
                    "messages": current_segment,
                    "topics": detected_topics or ["general"],
                    "start_time": current_segment[0].timestamp,
                    "end_time": current_segment[-1].timestamp
                })
                current_segment = []

        if current_segment:
            segments.append({
                "messages": current_segment,
                "topics": ["general"],
                "start_time": current_segment[0].timestamp,
                "end_time": current_segment[-1].timestamp
            })

        return segments
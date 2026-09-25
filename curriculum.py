"""Authoritative natural-disaster slide curriculum metadata.

Indexes are zero-based. Prompt text is unchanged from the starter curriculum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple


@dataclass(frozen=True)
class SlideSpec:
    index: int
    title: str
    prompt: str


SLIDES: Tuple[SlideSpec, ...] = (
    SlideSpec(
        index=0,
        title="Welcome & Overview",
        prompt=(
            "SLIDE 1: WELCOME & OVERVIEW\n\n"
            "Welcome the audience and briefly introduce the topic: Natural Disasters. "
            "Explain that this presentation will walk through what natural disasters are, why they occur, "
            "and how they affect people and the environment. "
            "Mention that questions are welcome at any time and that you will continue guiding them through the slides."
        ),
    ),
    SlideSpec(
        index=1,
        title="What Are Natural Disasters",
        prompt=(
            "SLIDE 2: WHAT ARE NATURAL DISASTERS\n\n"
            "Explain that natural disasters are extreme natural events that cause major damage to life, property, "
            "or the environment. Examples include earthquakes, floods, hurricanes, volcanic eruptions, and droughts. "
            "Emphasize that these events are caused by natural processes of the Earth."
        ),
    ),
    SlideSpec(
        index=2,
        title="Why Natural Disasters Happen",
        prompt=(
            "SLIDE 3: WHY NATURAL DISASTERS HAPPEN\n\n"
            "Describe the main reasons natural disasters occur: movement of tectonic plates, extreme weather patterns, "
            "volcanic activity, and climate-related changes. "
            "Briefly mention that some disasters are sudden while others develop slowly over time."
        ),
    ),
    SlideSpec(
        index=3,
        title="Major Types of Natural Disasters",
        prompt=(
            "SLIDE 4: MAJOR TYPES OF NATURAL DISASTERS\n\n"
            "Introduce the most common categories such as earthquakes, floods, cyclones, wildfires, landslides, "
            "and volcanic eruptions. "
            "Explain that each type has different causes and impacts depending on geography and climate."
        ),
    ),
    SlideSpec(
        index=4,
        title="Impact on People",
        prompt=(
            "SLIDE 5: IMPACT ON PEOPLE\n\n"
            "Explain how natural disasters affect communities: loss of life, injuries, destruction of homes, "
            "and displacement of families. "
            "Also mention disruption to healthcare, education, and daily life."
        ),
    ),
    SlideSpec(
        index=5,
        title="Environmental Effects",
        prompt=(
            "SLIDE 6: ENVIRONMENTAL EFFECTS\n\n"
            "Describe how natural disasters affect ecosystems: deforestation from wildfires, flooding of habitats, "
            "soil erosion, and pollution of water sources. "
            "Mention that while disasters cause destruction, some also reshape landscapes and ecosystems."
        ),
    ),
    SlideSpec(
        index=6,
        title="Preparedness and Safety",
        prompt=(
            "SLIDE 7: PREPAREDNESS AND SAFETY\n\n"
            "Explain how preparation can reduce damage and save lives. "
            "Discuss early warning systems, evacuation plans, emergency kits, and community awareness. "
            "Highlight that education and planning are key to disaster resilience."
        ),
    ),
    SlideSpec(
        index=7,
        title="Conclusion & Discussion",
        prompt=(
            "SLIDE 8: CONCLUSION & DISCUSSION\n\n"
            "Summarize that natural disasters are powerful natural events that can have serious impacts on society "
            "and the environment. "
            "Emphasize the importance of preparedness, scientific understanding, and community cooperation. "
            "Invite the audience to ask questions or request clarification on any slide."
        ),
    ),
)


def validate_curriculum(slides: Sequence[SlideSpec] = SLIDES) -> None:
    if len(slides) != 8:
        raise ValueError(f"Expected exactly 8 slides, found {len(slides)}")
    for i, slide in enumerate(slides):
        if slide.index != i:
            raise ValueError(f"Slide indexes must be contiguous; expected {i}, got {slide.index}")
        if not slide.title.strip():
            raise ValueError(f"Slide {i} title must be non-empty")
        if not slide.prompt.strip():
            raise ValueError(f"Slide {i} prompt must be non-empty")


validate_curriculum()


def slide_prompts() -> List[str]:
    return [slide.prompt for slide in SLIDES]


def slide_title(index: int) -> str:
    return SLIDES[index].title


TOTAL_SLIDES = len(SLIDES)

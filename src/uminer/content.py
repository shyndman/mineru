from __future__ import annotations

from typing import ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

type JsonDict = dict[str, object]
type BBox = tuple[int, int, int, int]


class MinerUModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)


class TextRun(MinerUModel):
    type: Literal["text"]
    content: str


type InlineContent = TextRun


class ImageSource(MinerUModel):
    path: str


class BaseBlock(MinerUModel):
    bbox: BBox | None = None


class TitleContent(MinerUModel):
    title_content: list[InlineContent]
    level: int | None = None


class TitleBlock(BaseBlock):
    type: Literal["title"]
    content: TitleContent


class ParagraphContent(MinerUModel):
    paragraph_content: list[InlineContent]


class ParagraphBlock(BaseBlock):
    type: Literal["paragraph"]
    content: ParagraphContent


class ListItem(MinerUModel):
    item_type: str
    item_content: list[InlineContent]


class ListContent(MinerUModel):
    list_type: str
    list_items: list[ListItem]


class ListBlock(BaseBlock):
    type: Literal["list"]
    content: ListContent


class ImageContent(MinerUModel):
    image_source: ImageSource
    image_caption: list[InlineContent] = []
    image_footnote: list[InlineContent] = []


class ImageBlock(BaseBlock):
    type: Literal["image"]
    content: ImageContent


class TableContent(MinerUModel):
    image_source: ImageSource | None = None
    table_caption: list[InlineContent] = []
    table_footnote: list[InlineContent] = []
    html: str | None = None


class TableBlock(BaseBlock):
    type: Literal["table"]
    content: TableContent


class PageHeaderContent(MinerUModel):
    page_header_content: list[InlineContent]


class PageHeaderBlock(BaseBlock):
    type: Literal["page_header"]
    content: PageHeaderContent


class PageFooterContent(MinerUModel):
    page_footer_content: list[InlineContent]


class PageFooterBlock(BaseBlock):
    type: Literal["page_footer"]
    content: PageFooterContent


class PageNumberContent(MinerUModel):
    page_number_content: list[InlineContent]


class PageNumberBlock(BaseBlock):
    type: Literal["page_number"]
    content: PageNumberContent


class UnknownBlock(BaseBlock):
    type: str
    content: object | None = None


type ContentBlock = (
    TitleBlock
    | ParagraphBlock
    | ListBlock
    | ImageBlock
    | TableBlock
    | PageHeaderBlock
    | PageFooterBlock
    | PageNumberBlock
    | UnknownBlock
)
type KnownContentBlock = (
    TitleBlock
    | ParagraphBlock
    | ListBlock
    | ImageBlock
    | TableBlock
    | PageHeaderBlock
    | PageFooterBlock
    | PageNumberBlock
)
_known_block_adapter: TypeAdapter[KnownContentBlock] = TypeAdapter(KnownContentBlock)


class ContentPage(MinerUModel):
    index: int
    blocks: list[ContentBlock]


class ContentList(MinerUModel):
    pages: list[ContentPage]

    @classmethod
    def from_mineru(cls, raw: object) -> ContentList:
        if not isinstance(raw, list):
            raise TypeError("Expected content_list to be an array of pages")
        pages = cast(list[object], raw)
        return cls(
            pages=[
                ContentPage(
                    index=index,
                    blocks=[_parse_block(block) for block in _page_blocks(page)],
                )
                for index, page in enumerate(pages)
            ]
        )


def _page_blocks(page: object) -> list[JsonDict]:
    if not isinstance(page, list):
        raise TypeError("Expected content_list page to be an array")
    raw_blocks = cast(list[object], page)
    blocks: list[JsonDict] = []
    for block in raw_blocks:
        if not isinstance(block, dict):
            raise TypeError("Expected content_list block to be an object")
        blocks.append(cast(JsonDict, block))
    return blocks


def _parse_block(block: JsonDict) -> ContentBlock:
    try:
        return _known_block_adapter.validate_python(block)
    except ValidationError:
        return UnknownBlock.model_validate(block)

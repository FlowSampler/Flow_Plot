"""GUI building blocks for gating_gui.GatingApp.

Each module holds one mixin class with the methods of one tab or topic; GatingApp
inherits from all of them. The mixins share state through `self` (attributes are
created in GatingApp.__init__ and the _build_*_tab methods)."""
